# backend/app/agent/tools/planning_tools.py

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

Implementation notes (LLM helper alignment):
- All structured LLM calls are delegated to app.agent.llm_helpers.invoke_structured.
- We retain local JSON Schema → Pydantic validation when a JSON Schema envelope is used.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

import structlog
from langchain_core.tools import tool
from pydantic import TypeAdapter

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service, coerce_json
from app.core.config import settings

# All structured outputs come from schemas (centralized)
from app.agent.schemas import (
    InitialPlan,
    CharacterCastingDecision,
    NormalizedTopic,
    CharacterArchetypeList,
    jsonschema_for,  # schema builders registry
)

# NEW: dynamic, data-driven topic/intent analysis
from app.agent.tools.intent_classification import analyze_topic

# NEW: canonical sets (from app config)
from app.agent.canonical_sets import canonical_for, count_hint_for  # type: ignore

# Centralized structured LLM invocation
from app.agent.llm_helpers import invoke_structured

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy_allows(kind: str, *, media_hint: bool) -> bool:
    """
    kind: 'web'
    If retrieval config is absent, allow (back-compat). Budget is enforced
    inside the concrete tool (web_search), so we only check policy here.
    """
    r = getattr(settings, "retrieval", None)
    if not r:
        return True
    policy = (getattr(r, "policy", "off") or "off").lower()
    if policy == "off":
        return False
    if kind == "web" and not bool(getattr(r, "allow_web", False)):
        return False
    if policy == "media_only" and not media_hint:
        return False
    return True

def _ensure_initial_plan(obj) -> InitialPlan:
    if isinstance(obj, InitialPlan):
        return obj
    return InitialPlan.model_validate(coerce_json(obj))

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
    - Fall back to data-driven analysis if anything fails.
    """
    logger.info("tool.normalize_topic.start", category_preview=(category or "")[:120])

    search_context = ""
    try:
        # Import locally to avoid import-time cycles
        from app.agent.tools.data_tools import web_search  # type: ignore
        # Gate by policy (budget is enforced inside web_search)
        if _policy_allows("web", media_hint=False):
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
        out = await invoke_structured(
            tool_name="topic_normalizer",
            messages=messages,
            response_model=NormalizedTopic,
            explicit_schema=jsonschema_for("topic_normalizer"),
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.normalize_topic.ok.llm", category=out.category, kind=out.outcome_kind, mode=out.creativity_mode)
        return out
    except Exception as e:
        logger.warning("tool.normalize_topic.llm_fallback", error=str(e))

    # Fallback: dynamic, data-driven analyzer (no network)
    a = analyze_topic(category)
    return NormalizedTopic(
        category=a["normalized_category"],
        outcome_kind=a["outcome_kind"],        # type: ignore[arg-type]
        creativity_mode=a["creativity_mode"],  # type: ignore[arg-type]
        rationale="Data-driven keyword classification fallback.",
        intent=a.get("intent"),
    )


@tool
async def plan_quiz(
    category: str,
    outcome_kind: Optional[str] = None,
    creativity_mode: Optional[str] = None,
    intent: Optional[str] = None,              
    names_only: Optional[bool] = None,         
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InitialPlan:
    """
    Produce a brief synopsis and a set of 4–6 ideal archetypes.

    Best practice:
    - Normalize the input category (deterministically) to feed the prompt.
    - Ask for Pydantic InitialPlan; never a bare list.
    """
    logger.info(
        "tool.plan_quiz.start",
        category=category,
        outcome_kind=outcome_kind,
        creativity_mode=creativity_mode,
        intent=intent,
        names_only=names_only,
    )

    # Fast path: caller supplied pre-analysis; treat `category` as normalized.
    skip_analysis = False
    if outcome_kind and creativity_mode and (intent is not None):
        norm = category
        okind = outcome_kind
        cmode = creativity_mode
        _intent = intent or "identify"
        _names_only = bool(names_only) if names_only is not None else False
        skip_analysis = True
    else:
        a = analyze_topic(category)
        norm = a["normalized_category"]
        okind = outcome_kind or a["outcome_kind"]
        cmode = creativity_mode or a["creativity_mode"]
        _intent = intent or a.get("intent", "identify")
        _names_only = a.get("names_only", False)

    try:
        # Canonical names (if any) are passed through to steer the model
        canon = canonical_for(norm)
        canonical_names = canon or []

        prompt = prompt_manager.get_prompt("initial_planner")
        messages = prompt.invoke(
            {
                "category": norm,
                "outcome_kind": okind,
                "creativity_mode": cmode,
                "intent": _intent,
                "canonical_names": canonical_names,
                "normalized_category": norm,  # harmless back-compat for older templates
            }
        ).messages
        plan = await invoke_structured(
            tool_name="initial_planner",
            messages=messages,
            response_model=InitialPlan,
            explicit_schema=jsonschema_for("initial_planner", category=norm),
            trace_id=trace_id,
            session_id=session_id,
        )

        plan = _ensure_initial_plan(plan)

        # Ensure a usable title when providers omit it, without re-analysis
        if not (getattr(plan, "title", None) or "").strip():
            default_title = f"Which {norm} Are You?" if _names_only else f"What {norm} Are You?"
            plan = InitialPlan(
                title=default_title,
                synopsis=plan.synopsis,
                ideal_archetypes=plan.ideal_archetypes,
            )

        # If canonical exists, override ideal_archetypes and add count hint
        if canon:
            plan = InitialPlan(
                title=plan.title,
                synopsis=plan.synopsis,
                ideal_archetypes=list(canon),
                ideal_count_hint=count_hint_for(norm) or len(canon),
            )

        logger.info("tool.plan_quiz.ok", archetypes=len(plan.ideal_archetypes), skip_analysis=skip_analysis)
        return plan
    except Exception as e:
        logger.error("tool.plan_quiz.fail", error=str(e), exc_info=True)
        return InitialPlan(
            title=f"What {norm} Are You?",
            synopsis=f"A fun quiz about {norm}.",
            ideal_archetypes=[],
        )


@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    seed_archetypes: Optional[List[str]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generate 4–6 outcome labels. For media/factual topics we do light retrieval
    and pass `search_context` into the prompt so the LLM can extract
    canonical names.

    Best practice:
    - Request an array of strings (primary path) per updated prompt.
    - Gracefully accept legacy outputs of CharacterArchetypeList.
    """
    logger.info("tool.generate_character_list.start", category=category)

    # Canonical short-circuit (strict, never-changing sets)
    canon = canonical_for(category)
    if canon:
        logger.info("tool.generate_character_list.canonical", count=len(canon))
        return list(canon)

    a = analysis or analyze_topic(category)
    norm = a["normalized_category"]
    is_media = bool(a["is_media"])
    domain = a.get("domain") or ""
    names_only = bool(a.get("names_only"))
    intent = a.get("intent", "identify")

    search_context = ""
    if is_media or names_only or a["creativity_mode"] == "factual" or a["outcome_kind"] in {"profiles", "characters"}:
        try:
            from app.agent.tools.data_tools import wikipedia_search, web_search, consume_retrieval_slot  # type: ignore
            base_title = (
                norm.removesuffix(" Characters")
                    .removesuffix(" Artists & Groups")
                    .strip()
            )
            # Prefer Wikipedia when allowed & within budget
            if bool(getattr(getattr(settings, "retrieval", None), "allow_wikipedia", False)):
                if consume_retrieval_slot(trace_id, session_id):
                    if is_media:
                        wiki_q = f"List of main characters in {base_title}"
                    elif domain == "music_artists_acts":
                        wiki_q = f"List of 1990s R&B musicians" if '90' in base_title else f"List of {base_title} artists"
                    elif domain == "sports_leagues_teams":
                        wiki_q = f"List of {base_title} teams"
                    else:
                        wiki_q = f"Official types for {norm}"
                    res = await wikipedia_search.ainvoke({"query": wiki_q})
                    if isinstance(res, str) and res.strip():
                        search_context = res

            # Fallback to web (policy/budget enforced inside web_search)
            if not search_context:
                if _policy_allows("web", media_hint=is_media):
                    if is_media:
                        web_q = f"Main characters in {base_title}"
                    elif domain == "music_artists_acts":
                        web_q = f"Notable {base_title} artists and groups"
                    elif domain == "sports_leagues_teams":
                        web_q = f"Top {base_title} teams/clubs"
                    else:
                        web_q = f"Canonical/official types for {norm}"
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
            "canonical_names": canonical_for(norm) or [],
            "search_context": search_context,
            "intent": intent,
        }
    ).messages

    # Primary path: array-of-strings per new prompt
    try:
        resp = await invoke_structured(
            tool_name="character_list_generator",
            messages=messages,
            response_model=CharacterArchetypeList,
            explicit_schema=jsonschema_for("character_list_generator", category=norm),
            trace_id=trace_id,
            session_id=session_id,
        )
        if not isinstance(resp, CharacterArchetypeList):
            # try to coerce before falling back
            try:
                resp = CharacterArchetypeList.model_validate(coerce_json(resp))
            except Exception:
                raise
        names = [n.strip() for n in (resp.archetypes or []) if isinstance(n, str) and n.strip()]
    except Exception as e:
        logger.warning("tool.generate_character_list.primary_parse_failed", error=str(e))
        # FALLBACK: accept a raw array of strings (use helper + TypeAdapter validation path)
        try:
            adapter = TypeAdapter(List[str])
            raw = await invoke_structured(
                tool_name="character_list_generator",
                messages=messages,
                response_model=adapter,
                trace_id=trace_id,
                session_id=session_id,
            )
            names = [str(n).strip() for n in (raw or []) if isinstance(n, (str, bytes)) and str(n).strip()]
        except Exception as e2:
            logger.error("tool.generate_character_list.fail", error=str(e2), exc_info=True)
            names = []

    if is_media or names_only:
        def _looks_like_name(s: str) -> bool:
            w = str(s).split()
            return any(tok[:1].isupper() for tok in w[:2]) or ("-" in s) or ("." in s)
        names = [n for n in names if n and _looks_like_name(n)]

    # Hard cap per product strategy (prefer far fewer; system should rarely hit this)
    cap = int(getattr(getattr(settings, "quiz", object()), "max_characters", 32))
    if len(names) > cap:
        names = names[:cap]

    logger.info("tool.generate_character_list.ok", count=len(names), media=is_media)
    return names


@tool
async def select_characters_for_reuse(
    category: str,
    ideal_archetypes: List[str],
    retrieved_characters: List[Dict[str, Any]],
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

    a = analyze_topic(category)
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
        out = await invoke_structured(
            tool_name="character_selector",
            messages=messages,
            response_model=CharacterCastingDecision,
            explicit_schema=jsonschema_for("character_selector"),
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
