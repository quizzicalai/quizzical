"""
Agent Tools: Planning & Strategy

Tools here are thin wrappers around prompt templates + LLM service.
They are used by the agent planner and by the bootstrap steps in graph.py.

Key updates in this rewrite:
- Robust topic analysis so ANY user topic is supported.
- If the topic is a media title (book, movie, TV show, play, musical, anime,
  video game, etc.), the generated "character list" is REQUIRED to be the
  CANONICAL characters from that work (no invented archetypes).
- Heuristics decide whether outcomes should be "characters", "types",
  "archetypes", or "profiles" and whether the tone should be "whimsical"
  or "grounded"—but we do NOT introduce new state keys nor change tool names.
- Backward compatibility is maintained: InitialPlan and tool names/signatures
  are unchanged, so graph.py and __init__.py need no edits.

Note: We pass guidance via the 'synopsis' and prompt context so existing
prompts remain usable, even if they were updated to expect extra variables.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Literal

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


# -------------------------
# Structured outputs
# -------------------------

class InitialPlan(BaseModel):
    """Output of the initial planning stage."""
    synopsis: str = Field(description="Engaging synopsis (2–3 sentences) for the quiz category.")
    ideal_archetypes: List[str] = Field(description="4–6 ideal character archetypes.")

class CharacterCastingDecision(BaseModel):
    """Decisions whether to reuse, improve, or create characters."""
    reuse: List[Dict] = Field(default_factory=list, description="Existing characters to reuse as-is.")
    improve: List[Dict] = Field(default_factory=list, description="Existing characters to improve.")
    create: List[str] = Field(default_factory=list, description="New archetypes to create from scratch.")

class NormalizedTopic(BaseModel):
    """
    Output of normalize_topic. Field 'category' is the normalized, quiz-ready
    category string (kept as 'category' for backward compatibility).
    """
    category: str = Field(description="Normalized quiz category (e.g., 'Gilmore Girls Characters', 'Type of Dog').")
    outcome_kind: Literal["characters", "types", "archetypes", "profiles"] = Field(
        description="What kind of outcomes the quiz should produce."
    )
    creativity_mode: Literal["whimsical", "balanced", "factual", "grounded"] = Field(
        description="How creative/grounded the content should be."
    )
    rationale: str = Field(description="Brief explanation of the normalization decision.")


# -------------------------
# Internal heuristics (no external deps)
# -------------------------

_MEDIA_HINT_WORDS = {
    "season","episode","saga","trilogy","universe","series","show","sitcom","drama",
    "film","movie","novel","book","manga","anime","cartoon","comic","graphic novel",
    "musical","play","opera","broadway","videogame","video game","game","franchise"
}
_SERIOUS_HINTS = {
    "disc","myers","mbti","enneagram","big five","ocean","hexaco","strengthsfinder",
    "attachment style","aptitude","assessment","clinical","medical","doctor","physician",
    "lawyer","attorney","engineer","accountant","scientist","resume","cv","career"
}
_TYPE_SYNONYMS = {"type","types","kind","kinds","style","styles","variety","varieties","flavor","flavors","breed","breeds"}

def _looks_like_media_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if any(w in lc for w in _MEDIA_HINT_WORDS):
        return True
    # Title-ish multi-word (e.g., "Gilmore Girls", "Lord of the Rings")
    if " " in t and t[:1].isupper():
        # Avoid obvious generic phrases like "Type of Dog"
        if not any(k in lc for k in _TYPE_SYNONYMS):
            return True
    return False

def _simple_singularize(noun: str) -> str:
    s = (noun or "").strip()
    if not s:
        return s
    lower = s.lower()
    if lower.endswith("ies") and len(s) > 3:
        return s[:-3] + "y"
    if lower.endswith("ses") and len(s) > 3:
        return s[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return s[:-1]
    return s

def _analyze_topic(category: str) -> Dict[str, str]:
    """
    Decide:
      - normalized_category: canonical framing ("<Media> Characters" or "Type of X" or original)
      - outcome_kind: one of {'characters','types','archetypes','profiles'}
      - creativity_mode: {'whimsical'|'balanced'|'grounded'|'factual'}
      - is_media: 'yes'|'no' (string for easy injection)
    """
    raw = (category or "").strip()
    lc = raw.casefold()
    is_media = _looks_like_media_title(raw)
    is_serious = any(h in lc for h in _SERIOUS_HINTS)

    # If user already wrote "... Characters", respect it
    if raw.endswith(" Characters") or raw.endswith(" characters"):
        is_media = True

    if is_serious:
        # Real frameworks/roles → factual "profiles"
        return {
            "normalized_category": raw or "General",
            "outcome_kind": "profiles",
            "creativity_mode": "factual",
            "is_media": "no",
        }

    if is_media:
        base = raw.removesuffix(" Characters").removesuffix(" characters").strip()
        norm = f"{base} Characters"
        return {
            "normalized_category": norm,
            "outcome_kind": "characters",
            "creativity_mode": "balanced",
            "is_media": "yes",
        }

    # Generic noun/phrase → "Type of X"
    tokens = raw.split()
    if len(tokens) <= 2 and raw.isalpha():
        singular = _simple_singularize(raw)
        return {
            "normalized_category": f"Type of {singular}",
            "outcome_kind": "types",
            "creativity_mode": "whimsical",
            "is_media": "no",
        }

    # If explicitly about "type", prefer types
    if any(k in lc for k in _TYPE_SYNONYMS):
        return {
            "normalized_category": raw,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "is_media": "no",
        }

    # Default: archetypes, balanced tone
    return {
        "normalized_category": raw or "General",
        "outcome_kind": "archetypes",
        "creativity_mode": "balanced",
        "is_media": "no",
    }


# -------------------------
# Tools
# -------------------------

@tool
async def normalize_topic(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NormalizedTopic:
    """
    Normalize a raw user topic into a quiz-ready category and guidance flags.
    Keeps the field name 'category' for compatibility with downstream tools.
    """
    logger.info("tool.normalize_topic.start", category_preview=category[:120])
    a = _analyze_topic(category)
    out = NormalizedTopic(
        category=a["normalized_category"],
        outcome_kind=a["outcome_kind"],  # type: ignore[arg-type]
        creativity_mode=a["creativity_mode"],  # type: ignore[arg-type]
        rationale="Heuristic normalization based on topic hints.",
    )
    logger.info("tool.normalize_topic.ok", normalized=out.category, outcome_kind=out.outcome_kind, mode=out.creativity_mode)
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
    """
    logger.info("tool.plan_quiz.start", category=category, outcome_kind=outcome_kind, creativity_mode=creativity_mode)

    # Derive guidance if not supplied
    if not (outcome_kind and creativity_mode):
        a = _analyze_topic(category)
        outcome_kind = outcome_kind or a["outcome_kind"]
        creativity_mode = creativity_mode or a["creativity_mode"]

    try:
        prompt = prompt_manager.get_prompt("initial_planner")
        messages = prompt.invoke({
            "category": category,
            "normalized_category": _analyze_topic(category)["normalized_category"],
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
        logger.error("tool.plan_quiz.fail", error=str(e), exc_info=True)
        # Safe minimal fallback
        return InitialPlan(synopsis=f"A fun quiz about {category}.", ideal_archetypes=[])


@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    seed_archetypes: Optional[List[str]] = None,  # OPTIONAL, backward-compatible
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generates a list of 4–6 outcome labels for the quiz.

    IMPORTANT behavior:
    - If the category is/looks like a specific media title, this returns the
      *actual canonical character names* from that work (no invented archetypes).
    - Otherwise, returns archetype/type-style outcome names as before.

    Compatible with the existing 'character_list_generator' prompt. We inject
    a clear directive into the synopsis and also pass normalized signals. If the
    prompt template ignores the extra fields, it's still safe.

    NEW:
    - Accepts an optional seed_archetypes list and forwards it to the prompt as 'archetypes'
      for templates that now expect that variable.
    - Also provides a safe 'title' (e.g., "Quiz: <normalized_category>") for templates
      that reference it.
    """
    logger.info("tool.generate_character_list.start", category=category)
    analysis = _analyze_topic(category)
    normalized_category = analysis["normalized_category"]
    is_media = analysis["is_media"] == "yes"
    safe_title = f"Quiz: {normalized_category}"

    directive = (
        "Directive: If this category refers to a specific TV show, movie, book, play, musical, anime, video game, "
        "or similar work of fiction, RETURN ONLY THE CANONICAL CHARACTER NAMES from that work (no invented archetypes). "
        "If it is not a specific work, return distinct, useful outcome labels appropriate for a 'What ___ are you?' quiz."
    )

    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke({
        "category": normalized_category,                        # preserve 'category'
        "synopsis": f"{synopsis}\n\n{directive}",
        "normalized_category": normalized_category,             # satisfy updated templates
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
        "title": safe_title,                                    # NEW: avoid KeyError on 'title'
        "archetypes": seed_archetypes or [],                    # NEW: optional seed for updated prompts
    }).messages

    class _ArchetypeList(BaseModel):
        archetypes: List[str]

    try:
        resp = await llm_service.get_structured_response(
            "character_list_generator", messages, _ArchetypeList, trace_id, session_id
        )
        names = [n.strip() for n in (resp.archetypes or []) if str(n).strip()]
        # Light post-filter when media: avoid obvious non-names like "The Mentor"
        if is_media:
            cleaned = []
            for n in names:
                if len(n.split()) >= 1:
                    cleaned.append(n)
            names = cleaned
        logger.info("tool.generate_character_list.ok", count=len(names), media=is_media)
        return names
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
    Decision engine: for each ideal outcome, decide to reuse / improve / create.
    """
    logger.info(
        "tool.select_characters_for_reuse.start",
        category=category,
        ideal_count=len(ideal_archetypes),
        retrieved_count=len(retrieved_characters),
    )
    prompt = prompt_manager.get_prompt("character_selector")
    analysis = _analyze_topic(category)
    normalized_category = analysis["normalized_category"]

    messages = prompt.invoke({
        "category": normalized_category,
        "ideal_archetypes": ideal_archetypes,
        "retrieved_characters": retrieved_characters,
        "normalized_category": normalized_category,             # harmless if template ignores
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
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
