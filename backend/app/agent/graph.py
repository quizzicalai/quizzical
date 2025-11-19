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
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

import structlog
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

from app.agent.canonical_sets import canonical_for, count_hint_for

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
from app.models.api import FinalResult

# Soft-import the batch character tool; gracefully fall back if unavailable
try:  # pragma: no cover - import guard
    from app.agent.tools.content_creation_tools import (  # type: ignore
        draft_character_profiles as tool_draft_character_profiles,  # batch mode
    )
except Exception:  # pragma: no cover - absent in some deployments
    tool_draft_character_profiles = None  # type: ignore[assignment]

from app.core.config import settings as _base_settings
from app.services.llm_service import coerce_json

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SettingsProxy:
    """ Proxy to allow dynamic overrides in tests via attribute setting."""
    def __init__(self, base):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name):
        ov = object.__getattribute__(self, "_overrides")
        if name in ov:
            return ov[name]
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_overrides")[name] = value

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

def _ensure_quiz_prefix_helper(title: str) -> str:
    """Ensures title starts with 'Quiz: '."""
    import re as _re
    t = (title or "").strip()
    if not t:
        return "Quiz: Untitled"
    t = _re.sub(r"(?i)^quiz\s*[:\-–—]\s*", "", t).strip()
    return f"Quiz: {t}"


def _analyze_topic_safe(category: str) -> dict:
    """Runs analyze_topic with broad error handling."""
    try:
        a = analyze_topic(category)
        # Ensure defaults if missing
        return {
            "normalized_category": a.get("normalized_category") or category,
            "outcome_kind": a.get("outcome_kind") or "types",
            "creativity_mode": a.get("creativity_mode") or "balanced",
            "names_only": bool(a.get("names_only")),
            "intent": a.get("intent") or "identify",
            "domain": a.get("domain") or "",
            **a # include any other keys
        }
    except Exception:
        return {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "names_only": False,
            "intent": "identify",
            "domain": "",
        }


async def _repair_archetypes_if_needed(
    archetypes: List[str],
    category: str,
    synopsis_text: str,
    analysis: Dict[str, Any],
    names_only: bool,
    trace_id: Optional[str],
    session_id: Optional[str]
) -> List[str]:
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


async def _try_batch_generation(
    archetypes: List[str],
    category: str,
    analysis: Dict,
    trace_id: Optional[str],
    session_id: Optional[str],
    timeout: int
) -> Dict[str, Optional[CharacterProfile]]:
    """Attempts to generate characters in a single batch call."""
    results_map: Dict[str, Optional[CharacterProfile]] = dict.fromkeys(archetypes)

    if tool_draft_character_profiles is None or len(archetypes) <= 1:
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
            try:
                prof = _validate_character_payload(raw)
                # Name lock
                if (prof.name or "").strip().casefold() != (req_name or "").strip().casefold():
                    prof = CharacterProfile(
                        name=req_name,
                        short_description=prof.short_description,
                        profile_text=prof.profile_text,
                        image_url=getattr(prof, "image_url", None),
                    )
                results_map[req_name] = prof
            except Exception as e:
                logger.debug("characters_node.batch.item_invalid", character=req_name, error=str(e))

        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        got = sum(1 for v in results_map.values() if v is not None)
        logger.debug("characters_node.batch.ok", produced=got, duration_ms=dt_ms)

    except Exception as e:
        logger.debug("characters_node.batch.fail", error=str(e))

    return results_map


async def _fill_missing_with_concurrency(
    results_map: Dict[str, Optional[CharacterProfile]],
    category: str,
    analysis: Dict,
    trace_id: Optional[str],
    session_id: Optional[str],
    concurrency: int,
    timeout: int,
    max_retries: int
) -> None:
    """Fills any None values in results_map using per-item calls with semaphores."""

    sem = asyncio.Semaphore(concurrency)

    async def _attempt(name: str) -> Optional[CharacterProfile]:
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

    logger.info(
        "bootstrap_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category_preview=str(category_raw)[:120],
        env=_env_name(),
    )

    # ---- Analyze topic locally (no LLM) ----
    a = _analyze_topic_safe(category_raw)
    category = a["normalized_category"]

    # ---- Single LLM call: plan the quiz ----
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
    canon = canonical_for(category)
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
    agent_plan_json: Dict[str, Any] = {
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
    archetypes: List[str] = state.get("ideal_archetypes") or []

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
    characters: List[CharacterProfile] = [results_map[n] for n in archetypes if results_map.get(n) is not None]  # type: ignore[list-item]

    logger.info(
        "characters_node.done",
        session_id=session_id,
        trace_id=trace_id,
        generated_count=len(characters),
    )

    out: Dict[str, Any] = {
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

def _process_baseline_tool_output(raw: Any) -> List[QuizQuestion]:
    """Converts raw tool output into list of QuizQuestions."""
    def _to_quiz_question(obj) -> QuizQuestion:
        if isinstance(obj, QuizQuestion):
            return obj
        # expect QuestionOut shape
        text = getattr(obj, "question_text", None) or (obj.get("question_text") if isinstance(obj, dict) else "")
        opts = getattr(obj, "options", None) or (obj.get("options") if isinstance(obj, dict) else [])
        norm_opts: List[Dict[str, str]] = []
        for o in opts or []:
            if hasattr(o, "model_dump"):
                o = o.model_dump()
            if isinstance(o, dict) and o.get("text"):
                item = {"text": str(o["text"])}
                if o.get("image_url"):
                    item["image_url"] = str(o["image_url"])
                norm_opts.append(item)
        return QuizQuestion.model_validate({"question_text": str(text), "options": norm_opts})

    items = getattr(raw, "questions", None) if raw is not None else []
    if items is None and isinstance(raw, list):
        items = raw
    return [_to_quiz_question(i) for i in (items or [])]

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
    characters: List[CharacterProfile] = state.get("generated_characters") or []
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
    questions_state: List[Dict[str, Any]] = []
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

async def _determine_decision_action(
    history_payload: list,
    characters_payload: list,
    synopsis_payload: dict,
    analysis: dict,
    trace_id: Optional[str],
    session_id: Optional[str],
    answered: int
) -> Tuple[str, float, str]:
    """Determines (action, confidence, character_name) via tool and rules."""
    max_q = int(getattr(getattr(settings, "quiz", object()), "max_total_questions", 20))
    min_early = int(getattr(getattr(settings, "quiz", object()), "min_questions_before_early_finish", 6))
    thresh = float(getattr(getattr(settings, "quiz", object()), "early_finish_confidence", 0.9))

    if answered >= max_q:
        return "FINISH_NOW", 1.0, ""

    # Tool Call
    action = "ASK_ONE_MORE_QUESTION"
    confidence = 0.0
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

    # Business Rules
    if answered >= max_q:
        final_action = "FINISH_NOW"
    elif answered < min_early:
        final_action = "ASK_ONE_MORE_QUESTION"
    elif action == "FINISH_NOW" and confidence < thresh:
        final_action = "ASK_ONE_MORE_QUESTION"
    else:
        final_action = action

    return final_action, confidence, name

def _resolve_winning_character(
    name: str, characters: List[CharacterProfile]
) -> Optional[CharacterProfile]:
    """Matches name against characters, falling back to index 0."""
    winning = None
    if name:
        for c in characters:
            cname = _safe_getattr(c, "name", "")
            if cname and cname.strip().casefold() == name.casefold():
                winning = c
                break
    if not winning and characters:
        winning = characters[0]
    return winning


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

    if answered < baseline_count:
        return {"should_finalize": False, "messages": [AIMessage(content="Awaiting baseline answers")]}

    # 1. Determine Action
    action, confidence, name = await _determine_decision_action(
        history_payload, characters_payload, synopsis_payload, analysis,
        trace_id, session_id, answered
    )

    if action != "FINISH_NOW":
        return {"should_finalize": False, "current_confidence": confidence}

    # 2. Resolve Winner
    winning = _resolve_winning_character(name, characters)
    if not winning:
        return {"should_finalize": False, "messages": [AIMessage(content="No winner available; ask one more.")]}

    # 3. Write Final Result
    try:
        category = state.get("category") or _safe_getattr(synopsis, "title", "").removeprefix("Quiz: ").strip()
        outcome_kind = state.get("outcome_kind") or "types"
        creativity_mode = state.get("creativity_mode") or "balanced"

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
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    analysis = state.get("topic_analysis") or {}

    history_payload = [h.model_dump() if hasattr(h, "model_dump") else h for h in (history or [])]
    existing = state.get("generated_questions") or []

    characters_payload = [c.model_dump() if hasattr(c, "model_dump") else c for c in (characters or [])]
    synopsis_payload = _to_plain(synopsis) or {"title": "", "summary": ""}

    q_raw = await tool_generate_next_question.ainvoke({
        "quiz_history": history_payload,
        "character_profiles": characters_payload,
        "synopsis": synopsis_payload,
        "analysis": analysis,
        "trace_id": trace_id,
        "session_id": str(session_id),
    })

    # Convert next QuestionOut → QuizQuestion → plain dict for state
    def _one_to_qq(obj) -> QuizQuestion:
        if isinstance(obj, QuizQuestion):
            return obj
        text = getattr(obj, "question_text", None) or (obj.get("question_text") if isinstance(obj, dict) else "")
        opts = getattr(obj, "options", None) or (obj.get("options") if isinstance(obj, dict) else [])
        norm_opts: List[Dict[str, str]] = []
        for o in opts or []:
            if hasattr(o, "model_dump"):
                o = o.model_dump()
            if isinstance(o, dict) and o.get("text"):
                item = {"text": str(o["text"])}
                if o.get("image_url"):
                    item["image_url"] = str(o["image_url"])
                norm_opts.append(item)
        return QuizQuestion.model_validate({"question_text": str(text), "options": norm_opts})

    qq = _one_to_qq(q_raw)
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


async def create_agent_graph():
    """
    Compile the graph with a checkpointer.

    Prefers Redis (AsyncRedisSaver). If unavailable or disabled via
    USE_MEMORY_SAVER=1, falls back to MemorySaver.
    """
    logger.info("graph.compile.start", env=_env_name())
    t0 = time.perf_counter()

    env = _env_name()
    use_memory_saver = os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}

    checkpointer = None
    cm = None  # async context manager (to close later)

    # Optional TTL in minutes for Redis saver
    ttl_env = os.getenv("LANGGRAPH_REDIS_TTL_MIN", "").strip()
    ttl_minutes: Optional[int] = None
    if ttl_env.isdigit():
        try:
            ttl_minutes = max(1, int(ttl_env))
        except Exception:
            ttl_minutes = None

    # Create saver
    if env in {"local", "dev", "development"} and use_memory_saver:
        checkpointer = MemorySaver()
        logger.info("graph.checkpointer.memory", env=env)
    else:
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
            logger.warning(
                "graph.checkpointer.redis.fail_fallback_memory",
                error=str(e),
                hint="Ensure Redis Stack with RedisJSON & RediSearch is available, or set USE_MEMORY_SAVER=1.",
            )
            checkpointer = MemorySaver()
            cm = None

    agent_graph = workflow.compile(checkpointer=checkpointer)

    # Attach handles so the app can close things on shutdown
    try:
        agent_graph._async_checkpointer = checkpointer
        agent_graph._redis_cm = cm
    except Exception:
        logger.debug("graph.checkpointer.attach.skip")

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
