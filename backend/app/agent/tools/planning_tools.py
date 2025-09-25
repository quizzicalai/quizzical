# backend/app/agent/tools/planning_tools.py
"""
Agent Tools: Planning & Strategy

Thin wrappers around prompt templates + LLM service.
Used by the planner/bootstrap steps in graph.py.

Updates per plan:
- `normalize_topic` now performs light web research and uses the
  `topic_normalizer` prompt for a structured decision, with heuristic
  fallback on any error.
- `generate_character_list` conditionally performs Wikipedia/Web search
  for factual/media topics and passes `search_context` into the
  `character_list_generator` prompt. It falls back to the prior purely
  generative path if research fails or is not needed.

Other behavior, tool names, and signatures remain unchanged.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Literal

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Structured outputs
# ---------------------------------------------------------------------------


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
    Output of normalize_topic. Field 'category' is the normalized, quiz-ready category string.
    """
    category: str = Field(description="Normalized quiz category (e.g., 'Gilmore Girls Characters', 'Type of Dog').")
    outcome_kind: Literal["characters", "types", "archetypes", "profiles"] = Field(
        description="What kind of outcomes the quiz should produce."
    )
    creativity_mode: Literal["whimsical", "balanced", "factual"] = Field(
        description="How creative/grounded the content should be."
    )
    rationale: str = Field(description="Brief explanation of the normalization decision.")

# ---------------------------------------------------------------------------
# Internal heuristics (kept as deterministic fallback)
# ---------------------------------------------------------------------------

_MEDIA_HINT_WORDS = {
    "season", "episode", "saga", "trilogy", "universe", "series", "show", "sitcom", "drama",
    "film", "movie", "novel", "book", "manga", "anime", "cartoon", "comic", "graphic novel",
    "musical", "play", "opera", "broadway", "videogame", "video game", "game", "franchise",
}
_SERIOUS_HINTS = {
    "disc", "myers", "mbti", "enneagram", "big five", "ocean", "hexaco", "strengthsfinder",
    "attachment style", "aptitude", "assessment", "clinical", "medical", "doctor", "physician",
    "lawyer", "attorney", "engineer", "accountant", "scientist", "resume", "cv", "career",
}
_TYPE_SYNONYMS = {"type", "types", "kind", "kinds", "style", "styles", "variety", "varieties", "flavor", "flavors", "breed", "breeds"}


def _looks_like_media_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if any(w in lc for w in _MEDIA_HINT_WORDS):
        return True
    if " " in t and t[:1].isupper() and not any(k in lc for k in _TYPE_SYNONYMS):
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
      - normalized_category
      - outcome_kind: {'characters','types','archetypes','profiles'}
      - creativity_mode: {'whimsical','balanced','factual'}
      - is_media: 'yes'|'no' (string for easy injection)
    """
    raw = (category or "").strip()
    lc = raw.casefold()
    is_media = _looks_like_media_title(raw)
    is_serious = any(h in lc for h in _SERIOUS_HINTS)

    if raw.endswith(" Characters") or raw.endswith(" characters"):
        is_media = True

    if is_serious:
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

    tokens = raw.split()
    if len(tokens) <= 2 and raw.isalpha():
        singular = _simple_singularize(raw)
        return {
            "normalized_category": f"Type of {singular}",
            "outcome_kind": "types",
            "creativity_mode": "whimsical",
            "is_media": "no",
        }

    if any(k in lc for k in _TYPE_SYNONYMS):
        return {
            "normalized_category": raw,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "is_media": "no",
        }

    return {
        "normalized_category": raw or "General",
        "outcome_kind": "archetypes",
        "creativity_mode": "balanced",
        "is_media": "no",
    }

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def normalize_topic(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NormalizedTopic:
    """
    Normalize a raw user topic into a quiz-ready category and guidance flags.

    NEW:
    - Performs a quick web search and feeds results to the `topic_normalizer`
      prompt for a structured decision.
    - Falls back to deterministic heuristics on any failure.
    """
    logger.info("tool.normalize_topic.start", category_preview=category[:120])

    # 1) Light research (non-fatal if it fails)
    search_context = ""
    try:
        # Import locally to avoid any potential circular import at module import time
        from app.agent.tools.data_tools import web_search  # type: ignore
        search_q = (
            f"Disambiguate topic: '{category}'. Is this a media/franchise, a personality test/framework, "
            f"or a general concept? Provide the most relevant identifiers (work title, franchise, test name)."
        )
        search_context = await web_search.ainvoke(
            {"query": search_q, "trace_id": trace_id, "session_id": session_id}
        )
        if not isinstance(search_context, str):
            search_context = ""
    except Exception as e:
        logger.debug("tool.normalize_topic.search.skip", reason=str(e))
        search_context = ""

    # 2) Ask LLM to normalize using prompt (with research), with heuristic fallback
    try:
        prompt = prompt_manager.get_prompt("topic_normalizer")
        messages = prompt.invoke(
            {"category": category, "search_context": search_context}
        ).messages
        out = await llm_service.get_structured_response(
            tool_name="topic_normalizer",
            messages=messages,
            response_model=NormalizedTopic,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info(
            "tool.normalize_topic.ok.llm",
            normalized=out.category,
            outcome_kind=out.outcome_kind,
            mode=out.creativity_mode,
        )
        return out
    except Exception as e:
        logger.warning("tool.normalize_topic.llm_fallback", error=str(e))

    # 3) Heuristic fallback (previous behavior)
    a = _analyze_topic(category)
    out = NormalizedTopic(
        category=a["normalized_category"],
        outcome_kind=a["outcome_kind"],        # type: ignore[arg-type]
        creativity_mode=a["creativity_mode"],  # type: ignore[arg-type]
        rationale="Heuristic normalization based on topic hints.",
    )
    logger.info(
        "tool.normalize_topic.ok.heuristic",
        normalized=out.category,
        outcome_kind=out.outcome_kind,
        mode=out.creativity_mode,
    )
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
    Returns:
      - synopsis (2–3 sentences)
      - ideal_archetypes (4–6 labels)

    Uses the "initial_planner" prompt. We pass normalized category as {category}.
    """
    logger.info("tool.plan_quiz.start", category=category, outcome_kind=outcome_kind, creativity_mode=creativity_mode)

    if not (outcome_kind and creativity_mode):
        a = _analyze_topic(category)
        outcome_kind = outcome_kind or a["outcome_kind"]
        creativity_mode = creativity_mode or a["creativity_mode"]

    norm = _analyze_topic(category)["normalized_category"]
    try:
        prompt = prompt_manager.get_prompt("initial_planner")
        messages = prompt.invoke(
            {
                "category": norm,
                "outcome_kind": outcome_kind,
                "creativity_mode": creativity_mode,
                # back-compat (safe to include)
                "normalized_category": norm,
            }
        ).messages
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
        return InitialPlan(synopsis=f"A fun quiz about {norm}.", ideal_archetypes=[])


@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    seed_archetypes: Optional[List[str]] = None,  # optional, backward compatible
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generates 4–6 outcome labels.

    NEW:
    - For media/factual topics, perform a quick Wikipedia/Web search and
      pass the resulting `search_context` to the prompt so the LLM can
      extract canonical names. For creative topics, fall back to purely
      generative behavior.
    """
    logger.info("tool.generate_character_list.start", category=category)

    a = _analyze_topic(category)
    normalized_category = a["normalized_category"]
    is_media = a["is_media"] == "yes"
    search_context = ""

    # Conditional research for factual/media topics
    if is_media or a["creativity_mode"] == "factual" or a["outcome_kind"] in {"profiles", "characters"}:
        try:
            # Local import to avoid any import-time cycles
            from app.agent.tools.data_tools import wikipedia_search, web_search  # type: ignore
            # Prefer a targeted Wikipedia query first
            base_title = normalized_category.removesuffix(" Characters").strip()
            wiki_q = f"List of main characters in {base_title}" if is_media else f"Official types for {normalized_category}"
            search_context = await wikipedia_search.ainvoke({"query": wiki_q})
            if not isinstance(search_context, str) or not search_context.strip():
                # Fallback to general web search
                web_q = (
                    f"Main characters in {base_title}"
                    if is_media
                    else f"Canonical/official types for {normalized_category}"
                )
                search_context = await web_search.ainvoke(
                    {"query": web_q, "trace_id": trace_id, "session_id": session_id}
                )
                if not isinstance(search_context, str):
                    search_context = ""
        except Exception as e:
            logger.debug("tool.generate_character_list.search.skip", reason=str(e))
            search_context = ""

    # Prompt with optional search context
    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke(
        {
            "category": normalized_category,
            "synopsis": synopsis,
            "creativity_mode": a["creativity_mode"],
            "search_context": search_context,
        }
    ).messages

    try:
        # Preferred: array of strings
        names = await llm_service.get_structured_response(
            "character_list_generator", messages, List[str], trace_id, session_id
        )
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
    except ValidationError:
        # Legacy: {"archetypes": [...]}
        class _ArchetypeList(BaseModel):
            archetypes: List[str]
        try:
            legacy = await llm_service.get_structured_response(
                "character_list_generator", messages, _ArchetypeList, trace_id, session_id
            )
            names = [str(n).strip() for n in (legacy.archetypes or []) if str(n).strip()]
        except Exception as e2:
            logger.error("tool.generate_character_list.fail", error=str(e2), exc_info=True)
            return []

    if is_media:
        # Light scrub for obviously non-name labels
        names = [n for n in names if n]

    logger.info("tool.generate_character_list.ok", count=len(names), media=is_media)
    return names


@tool
async def select_characters_for_reuse(
    category: str,
    ideal_archetypes: List[str],
    retrieved_characters: List[Dict],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterCastingDecision:
    """For each ideal outcome, decide to reuse / improve / create."""
    logger.info(
        "tool.select_characters_for_reuse.start",
        category=category,
        ideal_count=len(ideal_archetypes),
        retrieved_count=len(retrieved_characters),
    )
    a = _analyze_topic(category)
    normalized_category = a["normalized_category"]
    prompt = prompt_manager.get_prompt("character_selector")
    messages = prompt.invoke(
        {
            "category": normalized_category,
            "ideal_archetypes": ideal_archetypes,
            "retrieved_characters": retrieved_characters,
            "outcome_kind": a["outcome_kind"],
            "creativity_mode": a["creativity_mode"],
            # back-compat (safe to include)
            "normalized_category": normalized_category,
        }
    ).messages

    try:
        out = await llm_service.get_structured_response(
            "character_selector", messages, CharacterCastingDecision, trace_id, session_id
        )
        logger.info(
            "tool.select_characters_for_reuse.ok",
            reuse=len(out.reuse), improve=len(out.improve), create=len(out.create),
        )
        return out
    except Exception as e:
        logger.error("tool.select_characters_for_reuse.fail", error=str(e), exc_info=True)
        return CharacterCastingDecision()
