# backend/app/agent/graph.py
"""
Main Agent Graph (synopsis/characters first → gated questions)

This LangGraph builds a quiz in two phases:

1) User-facing preparation
   - bootstrap → deterministic synopsis + archetype list + agent_plan (via planning_tools)
   - generate_characters → detailed character profiles (BATCH-FIRST with safe fallback)
   These run during /quiz/start. The request returns once synopsis (and
   typically characters) are ready.

2) Gated question generation
   - Only when the client calls /quiz/proceed, the API flips a state flag
     `ready_for_questions=True`. On the next graph run, a router sends flow
     to `generate_baseline_questions`, then to the sink.

Design notes:
- Nodes are idempotent: re-running after END will not redo work that exists.
- Removes legacy planner/tools loop and any `.to_dict()` tool usage.
- Uses async Redis checkpointer per langgraph-checkpoint-redis guidance.
- Aligns with planning_tools.py: normalize_topic/plan_quiz/generate_character_list
  wrappers supply steering signals without changing state keys.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Literal

import structlog
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent._settings_proxy import SettingsProxy as _SettingsProxy
from app.agent.canonical_sets import (
    canonical_for,
    count_hint_for,
    is_blended_pilot_topic,
    min_items_for,
)

# Import canonical models from agent.state (re-exported from schemas)
from app.agent.state import CharacterProfile, GraphState, QuizQuestion, Synopsis
from app.agent.tools.content_creation_tools import (
    decide_next_step as tool_decide_next_step,
)
from app.agent.tools.content_creation_tools import (
    draft_character_profile as tool_draft_character_profile,
)
from app.agent.tools.content_creation_tools import (
    generate_baseline_questions as tool_generate_baseline_questions,
)
from app.agent.tools.content_creation_tools import (
    generate_next_question as tool_generate_next_question,
)
from app.agent.tools.content_creation_tools import (
    write_blended_profile as tool_write_blended_profile,
)
from app.agent.tools.content_creation_tools import (
    write_final_user_profile as tool_write_final_user_profile,
)
from app.agent.tools.intent_classification import analyze_topic

# Planning & content tools (wrappers; keep names for compatibility)
from app.agent.tools.planning_tools import (
    InitialPlan,
)
from app.agent.tools.planning_tools import (
    generate_character_list as tool_generate_character_list,
)
from app.agent.tools.planning_tools import (
    plan_quiz as tool_plan_quiz,
)
from app.core.config import is_production
from app.core.config import settings as _base_settings
from app.models.api import FinalResult
from app.services.llm_service import coerce_json

logger = structlog.get_logger(__name__)

# Soft-import the optional batch character tool. AC-QUALITY-R2-IMPORT-1/2:
# only ImportError is suppressed (real bugs surface), and we log once at
# startup so operators can confirm the missing-feature mode.
try:  # pragma: no cover - import guard
    from app.agent.tools.content_creation_tools import (  # type: ignore
        draft_character_profiles as tool_draft_character_profiles,  # batch mode
    )
except ImportError:  # pragma: no cover - absent in some deployments
    tool_draft_character_profiles = None  # type: ignore[assignment]
    logger.info(
        "agent.optional_tool_unavailable",
        tool="draft_character_profiles",
        mode="single-profile fallback",
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Export proxy so tests target graph_mod.settings
settings = _SettingsProxy(_base_settings)


def _env_name() -> str:
    try:
        return (settings.app.environment or "local").lower()
    except Exception:
        return "local"


def _to_plain(obj: Any) -> Any:
    """Return a plain Python object (dict/primitive) for Pydantic-like inputs; pass dicts through."""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return obj

def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely access attributes on models or keys on dicts."""
    if hasattr(obj, attr):
        try:
            return getattr(obj, attr)
        except Exception:
            return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _validate_character_payload(payload: Any) -> CharacterProfile:
    """
    Normalize any raw LLM payload into a valid CharacterProfile immediately.
    """
    if isinstance(payload, CharacterProfile):
        return payload
    data = coerce_json(payload)
    return CharacterProfile.model_validate(data)

# ---------------------------------------------------------------------------
# Node Logic Extractors (Refactoring for Complexity)
# ---------------------------------------------------------------------------

_QUIZ_PREFIX_RE = re.compile(r"(?i)^quiz\s*[:\-–—]\s*")


def _ensure_quiz_prefix_helper(title: str) -> str:
    """Ensure ``title`` starts with the canonical 'Quiz: ' prefix."""
    t = (title or "").strip()
    if not t:
        return "Quiz: Untitled"
    t = _QUIZ_PREFIX_RE.sub("", t).strip()
    return f"Quiz: {t}"


def _quiz_question_from_obj(obj: Any) -> QuizQuestion:
    """Coerce loose tool output (model / dict / partial) into a :class:`QuizQuestion`.

    Accepts ``QuizQuestion`` (returned unchanged), ``QuestionOut``-shaped
    Pydantic models, and plain dicts. Options without text are dropped; the
    optional ``image_url`` is preserved when present.
    """
    if isinstance(obj, QuizQuestion):
        return obj
    text = getattr(obj, "question_text", None) or (
        obj.get("question_text") if isinstance(obj, dict) else ""
    )
    raw_opts = getattr(obj, "options", None) or (
        obj.get("options") if isinstance(obj, dict) else []
    )
    norm_opts: list[dict[str, str]] = []
    for o in raw_opts or []:
        if hasattr(o, "model_dump"):
            o = o.model_dump()
        if isinstance(o, dict) and o.get("text"):
            item = {"text": str(o["text"])}
            if o.get("image_url"):
                item["image_url"] = str(o["image_url"])
            norm_opts.append(item)
    payload: dict[str, Any] = {"question_text": str(text), "options": norm_opts}
    # INSTRUMENT RIGOR: preserve the probed-dimension tag when present so the
    # adaptive coverage balancing survives dict round-trips (absent for all
    # non-instrument topics — payload unchanged).
    dim = getattr(obj, "dimension", None) or (
        obj.get("dimension") if isinstance(obj, dict) else None
    )
    if dim:
        payload["dimension"] = str(dim)
    return QuizQuestion.model_validate(payload)


def _analyze_topic_safe(category: str) -> dict:
    """Run :func:`analyze_topic` with broad error handling.

    Defaults are applied *after* the upstream analysis is spread, so a
    falsy upstream value (``None`` or ``""``) cannot silently clobber the
    intended fallback.
    """
    try:
        a = analyze_topic(category) or {}
    except Exception:
        a = {}
    merged: dict = {**a}
    merged["normalized_category"] = a.get("normalized_category") or category
    merged["outcome_kind"] = a.get("outcome_kind") or "types"
    merged["creativity_mode"] = a.get("creativity_mode") or "balanced"
    merged["names_only"] = bool(a.get("names_only"))
    merged["intent"] = a.get("intent") or "identify"
    merged["domain"] = a.get("domain") or ""
    return merged


async def _repair_archetypes_if_needed(
    archetypes: list[str],
    category: str,
    synopsis_text: str,
    analysis: dict[str, Any],
    names_only: bool,
    trace_id: str | None,
    session_id: str | None
) -> list[str]:
    """Checks counts/constraints and runs generator tool if repair needed."""
    min_chars = getattr(getattr(settings, "quiz", object()), "min_characters", 4)
    max_chars = getattr(getattr(settings, "quiz", object()), "max_characters", 32)

    needs_repair = (not archetypes) or (len(archetypes) < min_chars) or (len(archetypes) > max_chars)

    if not needs_repair and names_only:
        def _looks_like_name(s: str) -> bool:
            w = str(s).strip().split()
            return any(tok[:1].isupper() for tok in w[:2]) or ("-" in s) or ("." in s)
        if not all(_looks_like_name(n) for n in archetypes):
            needs_repair = True

    if not needs_repair:
        return archetypes

    # Attempt repair
    try:
        repaired = await tool_generate_character_list.ainvoke({
            "category": category,
            "synopsis": synopsis_text,
            "analysis": analysis,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        if isinstance(repaired, list):
            archetypes = repaired
        elif hasattr(repaired, "archetypes"):
            archetypes = list(repaired.archetypes or [])
    except Exception as e:
        logger.debug("bootstrap_node.archetypes.repair.skipped", reason=str(e))

    # Final clamp
    return [n.strip() for n in archetypes if isinstance(n, str) and n.strip()][:max_chars]


def _accept_batch_profile(req_name: str, raw: Any) -> CharacterProfile | None:
    """Validate one batch item; None = treat as missing (fallback regenerates).

    AC-EVAL-2026-07-02 (punchlist P8): a name-matched but EMPTY profile is a
    coverage MISS, not a result. The batch tool back-fills dropped names with
    blank placeholders to keep order; accepting them here would ship a blank
    profile to the user. Returning None leaves the slot for
    ``_fill_missing_with_concurrency`` to regenerate via profile_writer.
    """
    try:
        prof = _validate_character_payload(raw)
        if not (prof.profile_text or "").strip():
            logger.warning(
                "characters_node.batch.item_empty",
                character=req_name,
                detail="blank profile_text from batch; regenerating via profile_writer",
            )
            return None
        # Name lock
        if (prof.name or "").strip().casefold() != (req_name or "").strip().casefold():
            prof = CharacterProfile(
                name=req_name,
                short_description=prof.short_description,
                profile_text=prof.profile_text,
                image_url=getattr(prof, "image_url", None),
            )
        return prof
    except Exception as e:
        logger.debug("characters_node.batch.item_invalid", character=req_name, error=str(e))
        return None


async def _try_batch_generation(
    archetypes: list[str],
    category: str,
    analysis: dict,
    trace_id: str | None,
    session_id: str | None,
    timeout: int
) -> dict[str, CharacterProfile | None]:
    """Attempts to generate characters in a single batch call.

    AC-PERF-CHAR-1: We skip the batch attempt entirely when the archetype list
    is large enough that the structured output would likely overflow the
    ``profile_batch_writer`` token budget and return truncated JSON. Each
    profile is now ~120-220 words (~300-450 tokens) plus JSON overhead, so a
    13-outcome batch easily exceeds the configured ``max_output_tokens``.
    Letting it run wastes ~30s before falling back to per-character calls.

    The threshold is tunable via ``settings.quiz.batch_max_archetypes``
    (default ``6``). Batch is also skipped when ``<= 1`` archetype since there
    is nothing to batch.
    """
    results_map: dict[str, CharacterProfile | None] = dict.fromkeys(archetypes)

    if tool_draft_character_profiles is None or len(archetypes) <= 1:
        return results_map

    batch_cap = int(
        getattr(getattr(settings, "quiz", object()), "batch_max_archetypes", 6) or 6
    )
    if len(archetypes) > batch_cap:
        logger.info(
            "characters_node.batch.skipped",
            reason="archetype_count_exceeds_batch_cap",
            count=len(archetypes),
            cap=batch_cap,
        )
        return results_map

    try:
        t0 = time.perf_counter()
        payload = {
            "category": category,
            "character_names": archetypes,
            "analysis": analysis,
            "trace_id": trace_id,
            "session_id": str(session_id),
        }
        raw_batch = await asyncio.wait_for(
            tool_draft_character_profiles.ainvoke(payload),  # type: ignore
            timeout=timeout,
        )

        # Accept list or dict outputs; normalize to name->profile mapping
        pairs = []
        if isinstance(raw_batch, dict):
            pairs = raw_batch.items()
        else:
            # best-effort: if a list, align by index
            seq = list(raw_batch or [])
            for i, n in enumerate(archetypes):
                item = seq[i] if i < len(seq) else None
                pairs.append((n, item))

        for req_name, raw in pairs:
            if raw is None:
                continue
            prof = _accept_batch_profile(req_name, raw)
            if prof is not None:
                results_map[req_name] = prof

        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        got = sum(1 for v in results_map.values() if v is not None)
        logger.debug("characters_node.batch.ok", produced=got, duration_ms=dt_ms)

    except Exception as e:
        logger.debug("characters_node.batch.fail", error=str(e))

    return results_map


async def _fill_missing_with_concurrency(
    results_map: dict[str, CharacterProfile | None],
    category: str,
    analysis: dict,
    trace_id: str | None,
    session_id: str | None,
    concurrency: int,
    timeout: int,
    max_retries: int
) -> None:
    """Fills any None values in results_map using per-item calls with semaphores."""

    sem = asyncio.Semaphore(concurrency)

    async def _attempt(name: str) -> CharacterProfile | None:
        try:
            raw_payload = await asyncio.wait_for(
                tool_draft_character_profile.ainvoke({
                    "character_name": name,
                    "category": category,
                    "analysis": analysis,
                    "trace_id": trace_id,
                    "session_id": str(session_id),
                }),
                timeout=timeout,
            )
            prof = _validate_character_payload(raw_payload)
            # Name lock logic
            if (prof.name or "").strip().casefold() != (name or "").strip().casefold():
                prof = CharacterProfile(
                    name=name,
                    short_description=prof.short_description,
                    profile_text=prof.profile_text,
                    image_url=getattr(prof, "image_url", None),
                )
            return prof
        except Exception as e:
            logger.debug("characters_node.attempt.fail", character=name, error=str(e))
            return None

    async def _one(name: str) -> None:
        for attempt in range(max_retries + 1):
            try:
                async with sem:
                    prof = await _attempt(name)
                if prof:
                    results_map[name] = prof
                    return
            except Exception:
                pass
            if attempt < max_retries:
                await asyncio.sleep(0.5 + 0.5 * attempt)
        logger.warning("characters_node.profile.gave_up", character=name)

    missing = [n for n, v in results_map.items() if v is None]
    if missing:
        tasks = [asyncio.create_task(_one(name)) for name in missing]
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Node: bootstrap (deterministic synopsis + archetypes + agent_plan)
# ---------------------------------------------------------------------------


async def _bootstrap_node(state: GraphState) -> dict:
    """
    Create/ensure a synopsis and a target list of character archetypes.
    Idempotent: If a synopsis already exists, returns no-op.
    """
    if state.get("synopsis"):
        logger.debug("bootstrap_node.noop", reason="synopsis_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category_raw = state.get("category") or (
        state["messages"][0].content if state.get("messages") else ""
    )

    # "Try a different interpretation" (2026-07-02) — prior readings the user
    # rejected for this same typed topic. Threaded from /quiz/start into the
    # planner prompt; empty for a normal start (identical behaviour).
    rejected_interpretations = [
        s.strip()
        for s in (state.get("rejected_interpretations") or [])
        if isinstance(s, str) and s.strip()
    ]

    logger.info(
        "bootstrap_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category_preview=str(category_raw)[:120],
        rejected_interpretations_count=len(rejected_interpretations),
        env=_env_name(),
    )

    # ---- Analyze topic locally (no LLM) ----
    a = _analyze_topic_safe(category_raw)
    category = a["normalized_category"]

    # ---- Single LLM call: plan the quiz ----
    # NOTE (P1 cost): the §7.7.1 topic-knowledge classifier used to run here on
    # every /quiz/start (a paid LLM call for the common non-canonical topic),
    # but its result was never consumed — `resolve_model_for_tool` has no
    # production caller, no node read `state["topic_knowledge"]`, and the key
    # was stripped on the next Redis save. The classifier (planning_tools) and
    # its tests are retained for when adaptive tiering is actually wired.
    t0 = time.perf_counter()
    plan: InitialPlan
    try:
        plan = await tool_plan_quiz.ainvoke({
            "category": category,
            "outcome_kind": a["outcome_kind"],
            "creativity_mode": a["creativity_mode"],
            "intent": a["intent"],
            "names_only": a["names_only"],
            "trace_id": trace_id,
            "session_id": str(session_id),
            # Reinterpret reload: instructs the planner to produce a genuinely
            # different reading. None/absent for a normal start.
            "rejected_interpretations": rejected_interpretations or None,
        })
    except Exception as e:
        logger.warning("bootstrap_node.plan_quiz.fail", error=str(e), exc_info=True)
        # Fallback to a usable plan
        plan = InitialPlan(
            title=f"What {category} Are You?",
            synopsis=f"A fun quiz about {category}.",
            ideal_archetypes=["The Analyst", "The Dreamer", "The Realist"],
        )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("bootstrap_node.plan_quiz.ok", duration_ms=dt_ms)

    # ---- Build synopsis from the plan directly ----
    plan_title = getattr(plan, "title", None) or f"What {category} Are You?"
    synopsis_obj = Synopsis(
        title=_ensure_quiz_prefix_helper(plan_title),
        summary=getattr(plan, "synopsis", None) or "",
    )
    if a["names_only"] and synopsis_obj.summary:
        synopsis_obj.summary += " You'll answer a few questions and we’ll match you to a well-known name."

    # ---- Determine archetypes (Canonical > Planner > Repair) ----
    # Reinterpret reload: skip the canonical override — the canonical set IS
    # the default reading the user just rejected; forcing it back would make
    # the reload a no-op for canonical topics.
    canon = canonical_for(category) if not rejected_interpretations else None
    if canon:
        archetypes = list(canon)
        plan.ideal_count_hint = count_hint_for(category) or len(archetypes)
    else:
        archetypes = [n.strip() for n in (getattr(plan, "ideal_archetypes", None) or []) if isinstance(n, str) and n.strip()]

    archetypes = await _repair_archetypes_if_needed(
        archetypes, category, synopsis_obj.summary, a, a["names_only"], trace_id, session_id
    )

    # Final guard: never leave this node with zero archetypes
    if not archetypes:
        archetypes = ["The Analyst", "The Dreamer", "The Realist"]

    plan_summary = f"Planned '{category}'. Synopsis ready. Target characters: {archetypes}"

    # NEW — materialize a plain JSON agent_plan for persistence (no Pydantic objects)
    agent_plan_json: dict[str, Any] = {
        "title": (getattr(plan, "title", None) or f"What {category} Are You?").strip(),
        "synopsis": synopsis_obj.summary,
        "ideal_archetypes": list(archetypes),
    }
    if getattr(plan, "ideal_count_hint", None) is not None:
        try:
            agent_plan_json["ideal_count_hint"] = int(plan.ideal_count_hint)  # type: ignore[arg-type]
        except Exception:
            pass

    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "synopsis": synopsis_obj,
        "ideal_archetypes": archetypes,
        "agent_plan": agent_plan_json,
        "topic_analysis": a,
        "outcome_kind": a["outcome_kind"],
        "creativity_mode": a["creativity_mode"],
        "is_error": False,
        "error_message": None,
        "error_count": 0,
    }


# ---------------------------------------------------------------------------
# Node: generate_characters (BATCH-FIRST with safe fallback)
# ---------------------------------------------------------------------------


async def _generate_characters_node(state: GraphState) -> dict:
    """
    Create detailed character profiles for each archetype in an order-preserving, batch-first flow.
    Idempotent: If characters already exist, returns no-op.
    """
    if state.get("generated_characters"):
        logger.debug("characters_node.noop", reason="characters_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category")
    analysis = state.get("topic_analysis") or {}
    archetypes: list[str] = state.get("ideal_archetypes") or []

    if not archetypes:
        logger.warning("characters_node.no_archetypes", session_id=session_id, trace_id=trace_id)
        return {"messages": [AIMessage(content="No archetypes to generate characters for.")]}

    default_concurrency = min(4, max(1, len(archetypes)))
    concurrency = (
        getattr(getattr(settings, "quiz", object()), "character_concurrency", default_concurrency)
        or default_concurrency
    )
    per_call_timeout_s = getattr(getattr(settings, "llm", object()), "per_call_timeout_s", 30)
    max_retries = int(getattr(getattr(settings, "agent", object()), "max_retries", 3))

    logger.info(
        "characters_node.start",
        session_id=session_id,
        trace_id=trace_id,
        target_count=len(archetypes),
        category=category,
    )

    # 1. Try Batch
    results_map = await _try_batch_generation(
        archetypes, category, analysis, trace_id, session_id, per_call_timeout_s
    )

    # 2. Fill Missing
    await _fill_missing_with_concurrency(
        results_map, category, analysis, trace_id, session_id,
        concurrency, per_call_timeout_s, max_retries
    )

    # 3. Assemble
    characters: list[CharacterProfile] = [results_map[n] for n in archetypes if results_map.get(n) is not None]  # type: ignore[list-item]

    logger.info(
        "characters_node.done",
        session_id=session_id,
        trace_id=trace_id,
        generated_count=len(characters),
    )

    out: dict[str, Any] = {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles (batch-first).")],
        "is_error": False,
        "error_message": None,
    }
    if characters:
        out["generated_characters"] = characters
    return out


# ---------------------------------------------------------------------------
# Node: generate_baseline_questions (gated)
# ---------------------------------------------------------------------------
def _dedupe_questions_by_text(qs):
    seen, out = set(), []
    def norm(s):
        return " ".join(str(s).split()).casefold()

    for q in qs or []:
        qt = getattr(q, "question_text", None) or (q.get("question_text") if isinstance(q, dict) else "")
        key = norm(qt)
        if key and key not in seen:
            out.append(q)
            seen.add(key)
    return out

def _process_baseline_tool_output(raw: Any) -> list[QuizQuestion]:
    """Convert raw baseline-tool output into a list of :class:`QuizQuestion`."""
    items = getattr(raw, "questions", None) if raw is not None else []
    if items is None and isinstance(raw, list):
        items = raw
    return [_quiz_question_from_obj(i) for i in (items or [])]

async def _generate_baseline_questions_node(state: GraphState) -> dict:
    """
    Generate the initial set of baseline questions (single structured call).
    """
    # If questions already exist but baseline_ready is missing/False, set it once and return
    existing = state.get("generated_questions")
    if existing and not state.get("baseline_ready"):
        logger.debug("baseline_node.flag_backfill", reason="questions_exist_flag_missing")
        return {
            "baseline_ready": True,
            "baseline_count": len(existing),
            "messages": [AIMessage(content=f"Baseline questions already present: {len(existing)} (flag backfilled).")],
            "is_error": False,
            "error_message": None,
        }

    if state.get("baseline_ready"):
        logger.debug("baseline_node.noop", reason="baseline_already_ready")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    characters: list[CharacterProfile] = state.get("generated_characters") or []
    synopsis = state.get("synopsis")
    analysis = state.get("topic_analysis") or {}

    logger.info(
        "baseline_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category=category,
        characters=len(characters),
    )

    desired_n = 0
    try:
        desired_n = int(getattr(getattr(settings, "quiz", object()), "baseline_questions_n", 0))
    except Exception:
        pass

    t0 = time.perf_counter()
    questions_state: list[dict[str, Any]] = []
    try:
        characters_payload = [c.model_dump() if hasattr(c, "model_dump") else c for c in (characters or [])]
        synopsis_payload = _to_plain(synopsis) or {"title": "", "summary": ""}

        raw = await tool_generate_baseline_questions.ainvoke({
            "category": category,
            "character_profiles": characters_payload,
            "synopsis": synopsis_payload,
            "analysis": analysis,
            "trace_id": trace_id,
            "session_id": str(session_id),
            "num_questions": desired_n or None,
        })

        questions = _process_baseline_tool_output(raw)
        if desired_n > 0:
            questions = questions[:desired_n]
        questions = _dedupe_questions_by_text(questions)
        questions_state = [q.model_dump(mode="json", exclude_none=True) for q in questions]
    except Exception as e:
        logger.error("baseline_node.tool_fail", session_id=session_id, trace_id=trace_id, error=str(e), exc_info=True)
        questions_state = []

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "baseline_node.done",
        session_id=session_id,
        duration_ms=dt_ms,
        produced=len(questions_state),
    )

    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions_state)}")],
        "generated_questions": questions_state,
        "baseline_count": len(questions_state),
        "baseline_ready": True,
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: decide / finish / adaptive
# ---------------------------------------------------------------------------

def _effective_depth_bounds(category: str | None) -> tuple[int, int]:
    """Resolve the TOPIC-AWARE (effective floor, effective cap) for a category.

    Owner decision (2026-06-30): floor 12, HARD max 24, vary the floor by topic
    seriousness. Rigorous instruments (DISC, MBTI, Big Five, …) ask MORE via a
    per-instrument ``min_items`` in the canonical catalog / App-Config; casual or
    non-canonical topics collapse to the global floor.

      eff_min = clamp(max(global_floor, min_items_for(category) or 0),
                      depth_floor_min, HARD_MAX)
      eff_max = HARD_MAX  (NEVER exceeds 24)

    where ``HARD_MAX = min(max_total_questions, 24)`` (24 is the owner ceiling;
    config is validated <= 24 but we re-clamp defensively in case a stale proxy
    in tests sets a higher value). All three knobs are read LIVE from config.
    """
    quiz = getattr(settings, "quiz", object())
    global_floor = int(getattr(quiz, "min_questions_before_early_finish", 12))
    floor_min = int(getattr(quiz, "depth_floor_min", 12))
    # The absolute owner ceiling; the configured cap may be lower but never higher.
    hard_max = min(int(getattr(quiz, "max_total_questions", 24)), 24)

    per_instrument = 0
    try:
        mi = min_items_for(category)
        if isinstance(mi, int) and mi > 0:
            per_instrument = mi
    except Exception:
        per_instrument = 0

    eff_min = max(global_floor, per_instrument)
    # Clamp into [floor_min, hard_max]: never below the owner floor, never above
    # the hard cap (a rigorous min_items of 24 with hard_max 24 stays 24).
    eff_min = max(floor_min, min(eff_min, hard_max))
    return eff_min, hard_max


async def _determine_decision_action(
    history_payload: list,
    characters_payload: list,
    synopsis_payload: dict,
    analysis: dict,
    trace_id: str | None,
    session_id: str | None,
    answered: int,
    current_confidence: float = 0.0,
    category: str | None = None,
) -> tuple[str, float, str]:
    """Determines (action, confidence, character_name) via tool and rules.

    ``category`` feeds the topic-aware effective floor/cap (see
    ``_effective_depth_bounds``): rigorous instruments ask more questions before
    an early finish while casual topics use the global floor. When ``category``
    is None/non-canonical the floor collapses to the global floor.
    """
    eff_min, max_q = _effective_depth_bounds(category)
    min_early = eff_min
    thresh = float(getattr(getattr(settings, "quiz", object()), "early_finish_confidence", 0.9))

    if answered >= max_q:
        return "FINISH_NOW", 1.0, ""

    # Efficiency (#3): below the floor the business rule below FORCES
    # ASK_ONE_MORE_QUESTION regardless of the tool's verdict, so invoking the
    # paid decision_maker LLM call (gpt-4o-mini) only to discard its action is
    # pure waste. Short-circuit before the tool call (mirrors the answered>=max_q
    # early return) — saves ~4 calls + ~6s/quiz. We must NOT drop confidence to
    # 0.0 though: that would blank the FE "% confident" pill for the first ~4
    # questions (it previously showed the tool's rising value). Instead surface
    # a cheap, deterministic progress proxy that rises toward the floor WITHOUT
    # an LLM call; the real tool confidence takes over at/above the floor. Never
    # regress below any confidence already carried forward from state.
    if answered < min_early:
        proxy = round(min(0.6, (answered / max(min_early, 1)) * 0.6), 2)
        return "ASK_ONE_MORE_QUESTION", max(float(current_confidence or 0.0), proxy), ""

    # Tool Call
    action = "ASK_ONE_MORE_QUESTION"
    # UX-2026-07-02 (progress/closeness): seed with the carried confidence so a
    # tool FAILURE surfaces the last honest reading instead of regressing the
    # FE closeness cue to "no signal" (0.0). A successful tool call overwrites
    # this with its fresh value below (which may legitimately be lower).
    confidence = float(current_confidence or 0.0)
    name = ""
    try:
        decision = await tool_decide_next_step.ainvoke({
            "quiz_history": history_payload,
            "character_profiles": characters_payload,
            "synopsis": synopsis_payload,
            "analysis": analysis,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        action = getattr(decision, "action", "ASK_ONE_MORE_QUESTION")
        confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
        if confidence > 1.0:
            confidence = min(1.0, confidence / 100.0)
        name = (getattr(decision, "winning_character_name", "") or "").strip()
    except Exception as e:
        logger.error("decide_node.tool_fail", error=str(e))

    # Business Rules. NOTE: the answered>=max_q and answered<min_early cases are
    # already handled by the early returns above (the latter skips the tool call
    # entirely — see #3), so only the confidence-threshold gate remains here.
    if action == "FINISH_NOW" and confidence < thresh:
        final_action = "ASK_ONE_MORE_QUESTION"
    else:
        final_action = action

    return final_action, confidence, name

def _resolve_winning_character(
    name: str, characters: list[CharacterProfile]
) -> CharacterProfile | None:
    """Match the LLM-named character against the candidate list.

    Strict policy: never silently fall back to characters[0] when the LLM
    failed to emit a usable name (e.g. truncated JSON from a reasoning model
    that exhausted its output token budget on hidden reasoning). Returning
    None forces the caller to ask one more question instead of mis-assigning
    a profile to the user.
    """
    if not name or not characters:
        if not name:
            logger.warning(
                "decide_node.empty_winner_name",
                detail="LLM emitted empty winning_character_name; refusing silent fallback",
                num_candidates=len(characters or []),
            )
        return None
    for c in characters:
        cname = _safe_getattr(c, "name", "")
        if cname and cname.strip().casefold() == name.casefold():
            return c
    logger.warning(
        "decide_node.unmatched_winner_name",
        winner_name=name,
        candidates=[_safe_getattr(c, "name", "") for c in characters],
    )
    return None


async def _decide_or_finish_node(state: GraphState) -> dict:
    """Decide whether to finish or ask one more, robust to dict/model hydration."""
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("synopsis")
    characters = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    analysis = state.get("topic_analysis") or {}

    # Normalize payloads
    history_payload = [_to_plain(i) for i in (history or [])]
    characters_payload = [_to_plain(c) for c in (characters or [])]
    synopsis_payload = (
        synopsis.model_dump() if hasattr(synopsis, "model_dump")
        else (_to_plain(synopsis) or {"title": "", "summary": ""})
    )

    answered = len(history)
    baseline_count = int(state.get("baseline_count") or 0)
    # Topic-aware HARD cap (NEVER exceeds 24). The category drives the effective
    # floor inside _determine_decision_action and the forced-finish cap here, so
    # both sites agree on the same bound for this topic.
    category = state.get("category")
    _eff_min, max_q = _effective_depth_bounds(category)

    if answered < baseline_count:
        return {"should_finalize": False, "messages": [AIMessage(content="Awaiting baseline answers")]}

    # 1. Determine Action
    carried_confidence = float(state.get("current_confidence") or 0.0)
    action, confidence, name = await _determine_decision_action(
        history_payload, characters_payload, synopsis_payload, analysis,
        trace_id, session_id, answered, carried_confidence, category=category
    )

    if action != "FINISH_NOW":
        return {"should_finalize": False, "current_confidence": confidence}

    # 2. Resolve Winner
    winning = _resolve_winning_character(name, characters)
    if not winning:
        # At the HARD cap (answered >= max_q) we MUST finalize — the cap path
        # returns FINISH_NOW with an empty name (no tool call), so the strict
        # no-fallback policy would otherwise loop forever generating questions
        # 21, 22, 23… one paid LLM call per /quiz/next, and the user would never
        # reach a result (P1). Pick a deterministic fallback winner only in this
        # forced-finish case; non-cap unresolved winners still ask one more.
        if answered >= max_q and characters:
            winning = characters[0]
            logger.warning(
                "decide_node.forced_finish_fallback",
                reason="max_total_questions reached with no resolvable winner",
                answered=answered,
                max_q=max_q,
                picked=_safe_getattr(winning, "name", ""),
                had_name=bool(name),
            )
        else:
            # UX-2026-07-02 (progress/closeness): this is a real adaptive
            # iteration whose tool call produced a fresh confidence — carry it
            # into state like the plain ask-one-more branch does, so the FE
            # closeness cue keeps moving instead of silently dropping a beat.
            return {
                "should_finalize": False,
                "current_confidence": confidence,
                "messages": [AIMessage(content="No winner available; ask one more.")],
            }

    # 3. Write Final Result
    try:
        category = state.get("category") or _safe_getattr(synopsis, "title", "").removeprefix("Quiz: ").strip()
        outcome_kind = state.get("outcome_kind") or "types"
        creativity_mode = state.get("creativity_mode") or "balanced"

        # Blended-profile PILOT gate (2026-06-30): a true BLENDED-PROFILE result
        # is produced ONLY when the topic is canonically blended AND on the
        # App-Config allowlist (default ["disc"]). Every other topic — including
        # the also-blended Big Five, until the owner widens the list — takes the
        # unchanged single-character path below.
        pilot_allowlist = list(
            getattr(getattr(settings, "quiz", object()), "blended_outcome_pilot", []) or []
        )
        if is_blended_pilot_topic(category, pilot_allowlist):
            dimensions = canonical_for(category) or []
            logger.info(
                "decide_node.blended_pilot",
                category=category,
                dimension_count=len(dimensions),
            )
            final = await tool_write_blended_profile.ainvoke({
                "winning_character": _to_plain(winning),
                "quiz_history": history_payload,
                "dimensions": dimensions,
                "trace_id": trace_id,
                "session_id": str(session_id),
                "category": category,
                "creativity_mode": creativity_mode,
            })
            return {"final_result": final, "should_finalize": True, "current_confidence": confidence}

        final = await tool_write_final_user_profile.ainvoke({
            "winning_character": _to_plain(winning),
            "quiz_history": history_payload,
            "trace_id": trace_id,
            "session_id": str(session_id),
            "category": category,
            "outcome_kind": outcome_kind,
            "creativity_mode": creativity_mode,
        })
        return {"final_result": final, "should_finalize": True, "current_confidence": confidence}
    except Exception as e:
        logger.error("decide_node.final_result_fail", error=str(e))
        return {
            "final_result": FinalResult(
                title="Result Error",
                description="Failed to generate final profile.",
                image_url=None,
            ),
            "should_finalize": True,
            "current_confidence": confidence,
        }


async def _generate_adaptive_question_node(state: GraphState) -> dict:
    """
    Generate one adaptive question and append it to state in **state shape**.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("synopsis")
    characters: list[CharacterProfile] = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    analysis = state.get("topic_analysis") or {}

    history_payload = [h.model_dump() if hasattr(h, "model_dump") else h for h in (history or [])]
    existing = state.get("generated_questions") or []

    characters_payload = [c.model_dump() if hasattr(c, "model_dump") else c for c in (characters or [])]
    synopsis_payload = _to_plain(synopsis) or {"title": "", "summary": ""}

    # INSTRUMENT RIGOR (feat/instrument-rigor): the dimension tags of every
    # question generated so far (baseline + adaptive). For validated-instrument
    # topics the NQG uses these to target the LEAST-COVERED dimension; for all
    # other topics the list is empty and the tool ignores it.
    asked_dimensions: list[str] = []
    for q in existing:
        d = q.get("dimension") if isinstance(q, dict) else getattr(q, "dimension", None)
        if d:
            asked_dimensions.append(str(d))

    q_raw = await tool_generate_next_question.ainvoke({
        "quiz_history": history_payload,
        "character_profiles": characters_payload,
        "synopsis": synopsis_payload,
        "analysis": analysis,
        "trace_id": trace_id,
        "session_id": str(session_id),
        "asked_dimensions": asked_dimensions,
    })

    qq = _quiz_question_from_obj(q_raw)
    qd = qq.model_dump(mode="json", exclude_none=True)
    return {"generated_questions": [*existing, qd]}

# ---------------------------------------------------------------------------
# Node: assemble_and_finish (sink)
# ---------------------------------------------------------------------------


async def _assemble_and_finish(state: GraphState) -> dict:
    """
    Sink node: logs a compact summary. Safe whether or not questions exist.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    chars = state.get("generated_characters") or []
    qs = state.get("generated_questions") or []
    syn = state.get("synopsis")

    logger.info(
        "assemble.finish",
        session_id=session_id,
        trace_id=trace_id,
        has_synopsis=bool(syn),
        characters=len(chars),
        questions=len(qs),
    )

    summary = (
        f"Assembly summary → synopsis: {bool(syn)} | "
        f"characters: {len(chars)} | questions: {len(qs)}"
    )
    return {"messages": [AIMessage(content=summary)]}


# ---------------------------------------------------------------------------
# Router / conditionals
# ---------------------------------------------------------------------------


def _phase_router(state: GraphState) -> Literal["baseline", "adaptive", "end"]:
    """After characters: baseline if none yet; adaptive only after all baseline answered."""
    if not state.get("ready_for_questions"):
        return "end"
    have_baseline = bool(state.get("baseline_ready"))  # <-- explicit flag, not the list
    if not have_baseline:
        return "baseline"
    answered = len(state.get("quiz_history") or [])
    baseline_count = int(state.get("baseline_count") or 0)
    return "adaptive" if answered >= baseline_count else "end"


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------


workflow = StateGraph(GraphState)
logger.debug("graph.init", workflow_id=id(workflow))

# Nodes
workflow.add_node("bootstrap", _bootstrap_node)
workflow.add_node("generate_characters", _generate_characters_node)
workflow.add_node("generate_baseline_questions", _generate_baseline_questions_node)
workflow.add_node("decide_or_finish", _decide_or_finish_node)
workflow.add_node("generate_adaptive_question", _generate_adaptive_question_node)
workflow.add_node("assemble_and_finish", _assemble_and_finish)

# Entry
workflow.set_entry_point("bootstrap")

# Linear prep: bootstrap → generate_characters
workflow.add_edge("bootstrap", "generate_characters")

# Router: characters → (baseline | adaptive | END)
workflow.add_conditional_edges(
    "generate_characters",
    _phase_router,
    {"baseline": "generate_baseline_questions", "adaptive": "decide_or_finish", "end": END},
)

# If questions were generated, fan into sink then end
workflow.add_edge("generate_baseline_questions", "assemble_and_finish")
workflow.add_conditional_edges(
    "decide_or_finish",
    lambda s: "finish" if s.get("should_finalize") else "ask",
    {"finish": "assemble_and_finish", "ask": "generate_adaptive_question"},
)
workflow.add_edge("generate_adaptive_question", "assemble_and_finish")
workflow.add_edge("assemble_and_finish", END)

logger.debug(
    "graph.wired",
    edges=[
        ("bootstrap", "generate_characters"),
        ("generate_characters", "generate_baseline_questions/decide_or_finish/END via router"),
        ("generate_baseline_questions", "assemble_and_finish"),
        ("decide_or_finish", "assemble_and_finish/generate_adaptive_question via router"),
        ("generate_adaptive_question", "assemble_and_finish"),
        ("assemble_and_finish", "END"),
    ],
)

# ---------------------------------------------------------------------------
# Checkpointer factory (async Redis or in-memory)
# ---------------------------------------------------------------------------


# Pydantic state types that may be embedded in checkpointed state. These are
# registered with the checkpoint serde's msgpack allowlist so future LangGraph
# releases (which will block unregistered types by default) keep working.
_AGENT_STATE_MSGPACK_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("app.agent.schemas", "Synopsis"),
    ("app.agent.schemas", "CharacterProfile"),
    ("app.agent.schemas", "QuizQuestion"),
    ("app.agent.schemas", "QuestionOption"),
    ("app.agent.schemas", "QuestionOut"),
    ("app.agent.schemas", "QuestionAnswer"),
    ("app.agent.schemas", "InitialPlan"),
    ("app.agent.schemas", "NextStepDecision"),
)


def _register_state_types_with_serde(checkpointer) -> None:
    """Register agent Pydantic state types with the saver's msgpack allowlist.

    LangGraph's permissive default (``allowed_msgpack_modules=True``) emits
    deprecation warnings for unregistered types and is short-circuited by
    ``with_msgpack_allowlist`` (it returns ``self`` when the base allowlist
    is the permissive sentinel). We therefore construct a fresh
    ``JsonPlusSerializer`` with our explicit allowlist; ``SAFE_MSGPACK_TYPES``
    remain allowed implicitly.
    """
    serde = getattr(checkpointer, "serde", None)
    if serde is None:
        logger.debug("graph.serde.allowlist.skip", reason="serde_unavailable")
        return
    try:
        new_serde = JsonPlusSerializer(
            pickle_fallback=getattr(serde, "pickle_fallback", False),
            allowed_msgpack_modules=_AGENT_STATE_MSGPACK_ALLOWLIST,
        )
        checkpointer.serde = new_serde
        logger.info(
            "graph.serde.allowlist.registered",
            count=len(_AGENT_STATE_MSGPACK_ALLOWLIST),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("graph.serde.allowlist.fail", error=str(e))


def _use_memory_saver_requested() -> bool:
    """True when ``USE_MEMORY_SAVER`` env opts into the in-memory checkpointer."""
    return os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}


def _should_force_memory_saver(env: str | None) -> bool:
    """Decide whether to skip Redis and use ``InMemorySaver`` up front.

    Durability policy (P1): the in-memory checkpointer loses LangGraph's
    per-thread checkpoint on any process restart and is NOT shared across
    replicas/workers, so it must NEVER be selected by default in production.
    We only honor ``USE_MEMORY_SAVER`` as an *explicit local/dev opt-out*; in
    a production environment the flag is ignored and Redis is always attempted
    (a misconfigured prod flag should not silently downgrade durability).

    Returns ``True`` only when the env is non-production AND ``USE_MEMORY_SAVER``
    is truthy. Production always returns ``False`` (prefer Redis).
    """
    if is_production(env):
        return False
    return _use_memory_saver_requested()


async def create_agent_graph() -> CompiledStateGraph:
    """
    Compile the graph with a checkpointer.

    Prefers the durable Redis checkpointer (``AsyncRedisSaver``). Falls back to
    the in-memory saver only when explicitly requested via ``USE_MEMORY_SAVER=1``
    in a non-production environment, or when Redis init fails. In production a
    Redis failure is logged LOUDLY (``error``) because the resulting in-memory
    checkpointer is non-durable; it is never a silent downgrade.
    """
    logger.info("graph.compile.start", env=_env_name())
    t0 = time.perf_counter()

    env = _env_name()
    prod = is_production(env)

    checkpointer = None
    cm = None  # async context manager (to close later)

    # Optional TTL in minutes for Redis saver
    ttl_env = os.getenv("LANGGRAPH_REDIS_TTL_MIN", "").strip()
    ttl_minutes: int | None = None
    if ttl_env.isdigit():
        try:
            ttl_minutes = max(1, int(ttl_env))
        except Exception:
            ttl_minutes = None

    # Create saver
    if _should_force_memory_saver(env):
        # Explicit local/dev opt-out only (see _should_force_memory_saver).
        checkpointer = InMemorySaver()
        logger.info("graph.checkpointer.memory", env=env, reason="USE_MEMORY_SAVER")
    else:
        if prod and _use_memory_saver_requested():
            # A truthy flag in prod is ignored on purpose; surface it so the
            # operator knows the durable Redis path is still being attempted.
            logger.warning(
                "graph.checkpointer.memory_flag_ignored_in_prod",
                env=env,
                hint="USE_MEMORY_SAVER is honored only in non-prod; prod always uses Redis.",
            )
        try:
            # Try seconds-int API first
            if ttl_minutes:
                try:
                    cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_minutes * 60)
                except TypeError:
                    # Fallback to dict-style config if library expects a config mapping
                    cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl={
                        "default_ttl": ttl_minutes * 60,
                        "refresh_on_read": True,
                    })
            else:
                cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)

            checkpointer = await cm.__aenter__()
            # Some versions expose explicit setup
            if hasattr(checkpointer, "asetup"):
                await checkpointer.asetup()
            logger.info(
                "graph.checkpointer.redis.ok",
                ttl_minutes=ttl_minutes,
            )
        except Exception as e:
            # Redis checkpointer init failed. The app still rehydrates quiz
            # state from Redis/DB, so we keep the process up with an in-memory
            # saver rather than crashing startup — BUT in production this is a
            # durability downgrade (per-thread checkpoint lost on restart, not
            # shared across replicas), so make it LOUD (error) instead of a
            # quiet warning that nobody notices.
            log_fn = logger.error if prod else logger.warning
            log_fn(
                "graph.checkpointer.redis.fail_fallback_memory",
                env=env,
                durable=False,
                production=prod,
                error=str(e),
                hint=(
                    "Redis checkpointer init failed. AsyncRedisSaver requires a "
                    "Redis Stack server with the RedisJSON & RediSearch modules "
                    "(it issues JSON.SET / FT.CREATE); managed offerings such as "
                    "Azure Cache for Redis do NOT ship these modules. Provision a "
                    "module-enabled Redis (Redis Stack / Azure Managed Redis with "
                    "modules) and verify REDIS_URL connectivity. Until fixed, "
                    "LangGraph checkpoints are non-durable in this process."
                ),
                exc_info=prod,
            )
            checkpointer = InMemorySaver()
            cm = None

    agent_graph = workflow.compile(checkpointer=checkpointer)

    # Future-proof: explicitly register the agent's Pydantic state modules
    # with the checkpoint serializer's msgpack allowlist. LangGraph 1.x emits
    # a deprecation warning for unregistered types and will block them in a
    # future major release.
    _register_state_types_with_serde(checkpointer)

    # Attach handles so the app can close things on shutdown. LangGraph 1.x
    # CompiledStateGraph instances accept dynamic attribute assignment.
    agent_graph._async_checkpointer = checkpointer
    agent_graph._redis_cm = cm

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("graph.compile.done", duration_ms=dt_ms, entry_point="bootstrap")
    return agent_graph


async def aclose_agent_graph(agent_graph) -> None:
    """Close the async Redis checkpointer context when present."""
    cm = getattr(agent_graph, "_redis_cm", None)
    cp = getattr(agent_graph, "_async_checkpointer", None)

    if hasattr(cp, "aclose"):
        try:
            await cp.aclose()
            logger.info("graph.checkpointer.redis.aclose.ok")
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.aclose.fail", error=str(e), exc_info=True
            )

    if cm is not None and hasattr(cm, "__aexit__"):
        try:
            await cm.__aexit__(None, None, None)
            logger.info("graph.checkpointer.redis.closed")
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.close.fail", error=str(e), exc_info=True
            )
