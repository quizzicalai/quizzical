# backend/app/agent/tools/planning_tools.py
"""
Agent Tools: Planning & Strategy

Tools here are thin wrappers around prompt templates + LLM service.
They are used by the agent planner and by the bootstrap steps in graph.py.

This module has been extended with:
- normalize_topic: normalizes a raw user topic into a quiz-ready category,
  determines outcome kind (characters/types/archetypes/profiles), and selects
  a creativity mode (whimsical/balanced/factual). It preserves the public
  parameter name "category" to avoid breaking other tools.
- plan_quiz: wraps the initial planning (synopsis + archetype list) and
  accepts optional outcome_kind/creativity_mode hints while remaining
  compatible with existing prompts and callers.

No breaking changes to existing tool names, parameters, or return types.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Literal

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# -----------------------------------------------------------------------------
# Structured outputs
# -----------------------------------------------------------------------------

class NormalizedTopic(BaseModel):
    """
    Output of normalize_topic. Field 'category' is the normalized, quiz-ready
    category string (kept as 'category' for backward compatibility).
    """
    category: str = Field(description="Normalized quiz category (e.g., 'Gilmore Girls Characters', 'Type of Dog').")
    outcome_kind: Literal["characters", "types", "archetypes", "profiles"] = Field(
        description="What kind of outcomes the quiz should produce."
    )
    creativity_mode: Literal["whimsical", "balanced", "factual"] = Field(
        description="How creative/grounded the content should be."
    )
    rationale: str = Field(description="Brief explanation of the normalization decision.")

class InitialPlan(BaseModel):
    """Output of the initial planning stage."""
    synopsis: str = Field(description="Engaging synopsis (2–3 sentences) for the quiz category.")
    ideal_archetypes: List[str] = Field(description="4–6 ideal character archetypes.")

class CharacterCastingDecision(BaseModel):
    """Decisions whether to reuse, improve, or create characters."""
    reuse: List[Dict] = Field(default_factory=list, description="Existing characters to reuse as-is.")
    improve: List[Dict] = Field(default_factory=list, description="Existing characters to improve.")
    create: List[str] = Field(default_factory=list, description="New archetypes to create from scratch.")

# -----------------------------------------------------------------------------
# Internal helpers (no external dependencies)
# -----------------------------------------------------------------------------

_MEDIA_HINT_WORDS = {
    "season", "episode", "saga", "trilogy", "universe", "series", "show", "film", "movie",
    "novel", "book", "anime", "manga", "cartoon", "sitcom", "drama"
}
_SERIOUS_HINTS = {
    "disc", "myers", "mbti", "enneagram", "big five", "ocean", "hexaco", "strengthsfinder",
    "doctor", "physician", "surgeon", "nurse", "lawyer", "attorney", "engineer", "accountant",
    "scientist", "project manager", "product manager", "therapist", "counselor"
}
_TYPE_SYNONYMS = {"type", "kind", "style", "variety", "flavor", "breed"}

def _simple_singularize(noun: str) -> str:
    """Extremely light singularizer to keep things deterministic and dependency-free."""
    s = noun.strip()
    if not s:
        return s
    # very naive: 'ies' -> 'y', trailing 's' -> drop
    lower = s.lower()
    if lower.endswith("ies") and len(s) > 3:
        return s[:-3] + "y"
    if lower.endswith("ses") and len(s) > 3:
        return s[:-2]  # e.g., classes -> class (naive)
    if lower.endswith("s") and not lower.endswith("ss"):
        return s[:-1]
    return s

def _heuristic_normalize(category: str) -> NormalizedTopic:
    """
    Heuristic fallback when the topic_normalizer prompt is unavailable.
    Applies the rules described in the spec while keeping 'category' as the key.
    """
    raw = (category or "").strip()
    base = raw
    lc = raw.lower()

    # Detect serious/grounded topics
    is_serious = any(h in lc for h in _SERIOUS_HINTS)

    # If user already framed as "type of X" or includes a type synonym
    mentions_type = any(k in lc for k in _TYPE_SYNONYMS) or "what " in lc or "which " in lc

    # Heuristic: Media-ish proper title → Characters
    looks_like_title = (raw.istitle() and " " in raw) or any(w in lc for w in _MEDIA_HINT_WORDS)

    # One-word or generic plural → "Type of <X>"
    tokens = raw.split()
    is_generic_single = len(tokens) == 1 and raw.isalpha()

    if is_serious:
        # factual profiles (e.g., DISC, MBTI, roles)
        outcome_kind = "profiles"
        creativity_mode = "factual"
        norm = base
        if not mentions_type and not looks_like_title:
            # keep as-is for serious frameworks (e.g., "DISC", "Myers-Briggs")
            pass
        return NormalizedTopic(
            category=norm,
            outcome_kind=outcome_kind,
            creativity_mode=creativity_mode,
            rationale="Detected a serious/real-world framework or profession; use factual profiles."
        )

    if looks_like_title:
        # Media franchises/titles → Characters
        return NormalizedTopic(
            category=f"{base} Characters",
            outcome_kind="characters",
            creativity_mode="balanced",
            rationale="Looks like a media title; outcome should be characters from that world."
        )

    if is_generic_single and not mentions_type:
        singular = _simple_singularize(base)
        return NormalizedTopic(
            category=f"Type of {singular}",
            outcome_kind="types",
            creativity_mode="whimsical",
            rationale="Generic noun inferred; framing as 'Type of ...' for Buzzfeed-style outcomes."
        )

    # Default: treat as archetypes/types depending on wording
    if mentions_type:
        return NormalizedTopic(
            category=base if "type" in lc else f"Type of {base}",
            outcome_kind="types",
            creativity_mode="balanced",
            rationale="User phrased as a type/kind; keep 'types' framing."
        )

    return NormalizedTopic(
        category=base,
        outcome_kind="archetypes",
        creativity_mode="balanced",
        rationale="Could be framed as archetypes; keeping neutral creativity."
    )

# -----------------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------------

@tool
async def normalize_topic(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NormalizedTopic:
    """
    Normalize a raw user topic into a quiz-ready category and guidance flags.
    - Keeps the field name 'category' for compatibility with downstream tools.
    - Attempts to use the 'topic_normalizer' prompt if present; otherwise falls
      back to a deterministic heuristic.
    """
    logger.info("tool.normalize_topic.start", category_preview=category[:120])
    try:
        # Try prompt-based normalization if available
        prompt = prompt_manager.get_prompt("topic_normalizer")
        messages = prompt.invoke({"category": category}).messages
        out = await llm_service.get_structured_response(
            tool_name="topic_normalizer",
            messages=messages,
            response_model=NormalizedTopic,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.normalize_topic.ok", normalized=out.category, outcome_kind=out.outcome_kind, mode=out.creativity_mode)
        return out
    except Exception as e:
        logger.debug("tool.normalize_topic.fallback", reason=str(e))
        out = _heuristic_normalize(category)
        logger.info("tool.normalize_topic.heuristic", normalized=out.category, outcome_kind=out.outcome_kind, mode=out.creativity_mode)
        return out


@tool
async def plan_quiz(
    category: str,
    outcome_kind: Optional[str] = None,
    creativity_mode: Optional[str] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InitialPlan:
    """
    Wrapper over the initial planning step that returns:
      - synopsis (2–3 sentence text)
      - ideal_archetypes (4–6 labels)

    Backward-compatible behavior:
    - Still called "plan_quiz" here, but uses the existing "initial_planner" prompt.
    - Passes extra fields if the prompt uses them; they're harmless if unused.
    - If the prompt isn't available, falls back to an inline instruction.

    Tip: Call normalize_topic first and pass its fields here if you want
    stronger alignment, but this tool also works with just `category`.
    """
    logger.info("tool.plan_quiz.start", category=category, outcome_kind=outcome_kind, creativity_mode=creativity_mode)

    # Try the prompt registry first
    try:
        prompt = prompt_manager.get_prompt("initial_planner")
        messages = prompt.invoke({
            "category": category,
            "outcome_kind": outcome_kind,
            "creativity_mode": creativity_mode,
        }).messages
        plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=messages,
            response_model=InitialPlan,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.plan_quiz.ok", archetype_count=len(plan.ideal_archetypes))
        return plan
    except Exception as e:
        logger.debug("tool.plan_quiz.inline_fallback", reason=str(e))

    # Inline fallback if prompt is missing/misconfigured
    try:
        system = (
            "You are a master planner and creative director for a game studio. "
            "Produce a short, engaging synopsis and 4–6 distinct outcomes (archetypes). "
            "Outcomes should fit a Buzzfeed-style 'What ___ are you?' quiz."
        )
        hints = []
        if outcome_kind:
            hints.append(f"Outcome kind: {outcome_kind}.")
        if creativity_mode:
            hints.append(f"Creativity mode: {creativity_mode}.")
        user = (
            f"Create an initial plan for a personality quiz about '{category}'. "
            f"{' '.join(hints)}\n"
            "Return JSON with keys: 'synopsis' (2–3 sentences) and 'ideal_archetypes' (4–6 items)."
        )
        plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_model=InitialPlan,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.plan_quiz.ok_fallback", archetype_count=len(plan.ideal_archetypes))
        return plan
    except Exception as e:
        logger.error("tool.plan_quiz.fail", error=str(e), exc_info=True)
        # Safe minimal fallback
        return InitialPlan(synopsis=f"A fun quiz about {category}.", ideal_archetypes=[])


@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generates a list of 4–6 creative character archetypes for the quiz.
    """
    logger.info("tool.generate_character_list.start", category=category)
    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke({"category": category, "synopsis": synopsis}).messages

    class _ArchetypeList(BaseModel):
        archetypes: List[str]

    try:
        resp = await llm_service.get_structured_response(
            "character_list_generator", messages, _ArchetypeList, trace_id, session_id
        )
        logger.info("tool.generate_character_list.ok", count=len(resp.archetypes))
        return resp.archetypes
    except ValidationError as e:
        logger.error("tool.generate_character_list.validation", error=str(e), exc_info=True)
        return []
    except Exception as e:
        logger.error("tool.generate_character_list.fail", error=str(e), exc_info=True)
        return []


@tool
async def select_characters_for_reuse(
    category: str,
    ideal_archetypes: List[str],
    retrieved_characters: List[Dict],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterCastingDecision:
    """
    Decision engine: for each ideal archetype, decide to reuse / improve / create.
    """
    logger.info(
        "tool.select_characters_for_reuse.start",
        category=category,
        ideal_count=len(ideal_archetypes),
        retrieved_count=len(retrieved_characters),
    )
    prompt = prompt_manager.get_prompt("character_selector")
    messages = prompt.invoke({
        "category": category,
        "ideal_archetypes": ideal_archetypes,
        "retrieved_characters": retrieved_characters,
    }).messages
    try:
        out = await llm_service.get_structured_response(
            "character_selector", messages, CharacterCastingDecision, trace_id, session_id
        )
        logger.info(
            "tool.select_characters_for_reuse.ok",
            reuse=len(out.reuse), improve=len(out.improve), create=len(out.create)
        )
        return out
    except Exception as e:
        logger.error("tool.select_characters_for_reuse.fail", error=str(e), exc_info=True)
        return CharacterCastingDecision()
