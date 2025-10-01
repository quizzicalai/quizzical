"""
Agent Tools: Planning & Strategy (strict structured outputs)

This module provides thin wrappers around prompt templates and the shared
LLM service. Each tool ALWAYS asks for a Pydantic model response and
*never* a bare typing container, so the JSON schema sent to the model is
strict and consistent.

Key practices:
- Response models live in app.agent.schemas (single source of truth).
- Robust, side-effect-free heuristics for fallback (no network IO on failure).
- Defensive logging; no mutation of global services.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Literal

import structlog
from langchain_core.tools import tool

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

# All structured outputs come from schemas (centralized)
from app.agent.schemas import (
    InitialPlan,
    CharacterCastingDecision,
    NormalizedTopic,
    CharacterArchetypeList,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal heuristics (deterministic fallback if LLM or retrieval fails)
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
_TYPE_SYNONYMS = {
    "type", "types", "kind", "kinds", "style", "styles", "variety", "varieties",
    "flavor", "flavors", "breed", "breeds",
}


def _looks_like_media_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if any(w in lc for w in _MEDIA_HINT_WORDS):
        return True
    # Title-cased multi-word phrases are often media
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
      - is_media: 'yes'|'no'
    """
    raw = (category or "").strip()
    lc = raw.casefold()
    is_media = _looks_like_media_title(raw) or raw.endswith(" Characters") or raw.endswith(" characters")
    is_serious = any(h in lc for h in _SERIOUS_HINTS)

    if is_serious:
        return {
            "normalized_category": raw or "General",
            "outcome_kind": "profiles",
            "creativity_mode": "factual",
            "is_media": "no",
        }

    if is_media:
        base = raw.removesuffix(" Characters").removesuffix(" characters").strip()
        return {
            "normalized_category": f"{base} Characters",
            "outcome_kind": "characters",
            "creativity_mode": "balanced",
            "is_media": "yes",
        }

    tokens = raw.split()
    if len(tokens) <= 2 and raw.replace(" ", "").isalpha():
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
    Normalize a raw user topic into a quiz-ready category and steering flags.

    Best practice:
    - Perform light web search (non-fatal) and pass into the prompt.
    - Ask for a strict Pydantic model (NormalizedTopic).
    - Fall back to deterministic heuristics if anything fails.
    """
    logger.info("tool.normalize_topic.start", category_preview=(category or "")[:120])

    search_context = ""
    try:
        # Import locally to avoid import-time cycles
        from app.agent.tools.data_tools import web_search  # type: ignore
        q = (
            f"Disambiguate the topic '{category}'. Is it media/franchise, personality framework, "
            f"or a general concept? Provide identifiers (title, franchise, test name)."
        )
        res = await web_search.ainvoke({"query": q, "trace_id": trace_id, "session_id": session_id})
        if isinstance(res, str):
            search_context = res
    except Exception as e:
        logger.debug("tool.normalize_topic.search.skip", reason=str(e))

    # Primary path: structured LLM
    try:
        prompt = prompt_manager.get_prompt("topic_normalizer")
        messages = prompt.invoke({"category": category, "search_context": search_context}).messages
        out = await llm_service.get_structured_response(
            tool_name="topic_normalizer",
            messages=messages,
            response_model=NormalizedTopic,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.normalize_topic.ok.llm", category=out.category, kind=out.outcome_kind, mode=out.creativity_mode)
        return out
    except Exception as e:
        logger.warning("tool.normalize_topic.llm_fallback", error=str(e))

    # Heuristic fallback (no network)
    a = _analyze_topic(category)
    return NormalizedTopic(
        category=a["normalized_category"],
        outcome_kind=a["outcome_kind"],        # type: ignore[arg-type]
        creativity_mode=a["creativity_mode"],  # type: ignore[arg-type]
        rationale="Heuristic normalization based on topic & hint words.",
    )


@tool
async def plan_quiz(
    category: str,
    outcome_kind: Optional[str] = None,
    creativity_mode: Optional[str] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InitialPlan:
    """
    Produce a brief synopsis and a set of 4–6 ideal archetypes.

    Best practice:
    - Normalize the input category (deterministically) to feed the prompt.
    - Ask for Pydantic InitialPlan; never a bare list.
    """
    logger.info("tool.plan_quiz.start", category=category, outcome_kind=outcome_kind, creativity_mode=creativity_mode)

    a = _analyze_topic(category)
    okind = outcome_kind or a["outcome_kind"]
    cmode = creativity_mode or a["creativity_mode"]
    norm = a["normalized_category"]

    try:
        prompt = prompt_manager.get_prompt("initial_planner")
        messages = prompt.invoke(
            {
                "category": norm,
                "outcome_kind": okind,
                "creativity_mode": cmode,
                "normalized_category": norm,  # harmless back-compat for older templates
            }
        ).messages
        plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=messages,
            response_model=InitialPlan,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.plan_quiz.ok", archetypes=len(plan.ideal_archetypes))
        return plan
    except Exception as e:
        logger.error("tool.plan_quiz.fail", error=str(e), exc_info=True)
        return InitialPlan(synopsis=f"A fun quiz about {norm}.", ideal_archetypes=[])


@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    seed_archetypes: Optional[List[str]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generate 4–6 outcome labels. For media/factual topics we do light retrieval
    and pass `search_context` into the prompt so the LLM can extract
    canonical names.

    Best practice:
    - Request a *model* (CharacterArchetypeList) in structured output.
    - Gracefully accept legacy outputs if provider returns a plain list.
    """
    logger.info("tool.generate_character_list.start", category=category)

    a = _analyze_topic(category)
    norm = a["normalized_category"]
    is_media = a["is_media"] == "yes"

    search_context = ""
    if is_media or a["creativity_mode"] == "factual" or a["outcome_kind"] in {"profiles", "characters"}:
        try:
            from app.agent.tools.data_tools import wikipedia_search, web_search  # type: ignore
            base_title = norm.removesuffix(" Characters").strip()
            wiki_q = f"List of main characters in {base_title}" if is_media else f"Official types for {norm}"
            res = await wikipedia_search.ainvoke({"query": wiki_q})
            if isinstance(res, str) and res.strip():
                search_context = res
            else:
                web_q = (f"Main characters in {base_title}" if is_media else f"Canonical/official types for {norm}")
                res2 = await web_search.ainvoke({"query": web_q, "trace_id": trace_id, "session_id": session_id})
                if isinstance(res2, str):
                    search_context = res2
        except Exception as e:
            logger.debug("tool.generate_character_list.search.skip", reason=str(e))

    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke(
        {
            "category": norm,
            "synopsis": synopsis,
            "creativity_mode": a["creativity_mode"],
            "search_context": search_context,
        }
    ).messages

    # Primary path: strict model
    try:
        resp = await llm_service.get_structured_response(
            tool_name="character_list_generator",
            messages=messages,
            response_model=CharacterArchetypeList,
            trace_id=trace_id,
            session_id=session_id,
        )
        names = [n.strip() for n in (resp.archetypes or []) if isinstance(n, str) and n.strip()]
    except Exception as e:
        logger.warning("tool.generate_character_list.legacy_parse", error=str(e))
        # Legacy tolerance: some older templates/tools return a raw string list
        try:
            raw = await llm_service.get_structured_response(
                tool_name="character_list_generator",
                messages=messages,
                response_model=list,  # tolerate raw list in legacy providers
                trace_id=trace_id,
                session_id=session_id,
            )
            names = [str(n).strip() for n in (raw or []) if str(n).strip()]
        except Exception as e2:
            logger.error("tool.generate_character_list.fail", error=str(e2), exc_info=True)
            names = []

    if is_media:
        # Make sure labels look like names (trivial scrub only)
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
    """
    For each ideal outcome, decide whether to reuse, improve, or create.

    Best practice:
    - Request the CharacterCastingDecision model explicitly.
    """
    logger.info(
        "tool.select_characters_for_reuse.start",
        category=category,
        ideal_count=len(ideal_archetypes or []),
        retrieved_count=len(retrieved_characters or []),
    )

    a = _analyze_topic(category)
    norm = a["normalized_category"]

    prompt = prompt_manager.get_prompt("character_selector")
    messages = prompt.invoke(
        {
            "category": norm,
            "ideal_archetypes": ideal_archetypes or [],
            "retrieved_characters": retrieved_characters or [],
            "outcome_kind": a["outcome_kind"],
            "creativity_mode": a["creativity_mode"],
            "normalized_category": norm,  # harmless for older templates
        }
    ).messages

    try:
        out = await llm_service.get_structured_response(
            tool_name="character_selector",
            messages=messages,
            response_model=CharacterCastingDecision,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info(
            "tool.select_characters_for_reuse.ok",
            reuse=len(out.reuse), improve=len(out.improve), create=len(out.create),
        )
        return out
    except Exception as e:
        logger.error("tool.select_characters_for_reuse.fail", error=str(e), exc_info=True)
        return CharacterCastingDecision()
