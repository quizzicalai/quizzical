"""
Main Agent Graph (synopsis/characters first → gated questions)

This LangGraph builds a quiz in two phases:

1) User-facing preparation
   - bootstrap → deterministic synopsis + archetype list (via planning_tools)
   - generate_characters → detailed character profiles (PARALLEL)
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
from typing import Any, Dict, List, Optional, Literal

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

# Import canonical models from agent.state (re-exported from schemas)
from app.agent.state import GraphState, Synopsis, CharacterProfile, QuizQuestion

# Planning & content tools (wrappers; keep names for compatibility)
from app.agent.tools.planning_tools import (
    InitialPlan,
    normalize_topic as tool_normalize_topic,
    plan_quiz as tool_plan_quiz,
    generate_character_list as tool_generate_character_list,
)
from app.agent.tools.content_creation_tools import (
    generate_baseline_questions as tool_generate_baseline_questions,
    generate_next_question as tool_generate_next_question,
    decide_next_step as tool_decide_next_step,
    write_final_user_profile as tool_write_final_user_profile,
    generate_category_synopsis as tool_generate_category_synopsis,
    draft_character_profile as tool_draft_character_profile,
)

from app.core.config import settings
from app.services.llm_service import llm_service, coerce_json

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_name() -> str:
    try:
        return (settings.app.environment or "local").lower()
    except Exception:
        return "local"


def _safe_len(x):
    try:
        return len(x)  # type: ignore[arg-type]
    except Exception:
        return None


def _validate_synopsis_payload(payload: Any) -> Synopsis:
    """
    Normalize any raw LLM payload into a valid Synopsis immediately.
    Prevents leaking dicts/strings into state/Redis.
    """
    if isinstance(payload, Synopsis):
        return payload
    data = coerce_json(payload)
    # Legacy guard: map synopsis_text -> summary if needed.
    if isinstance(data, dict) and "synopsis_text" in data and "summary" not in data:
        data = {**data, "summary": data.get("synopsis_text")}
    return Synopsis.model_validate(data)


def _validate_character_payload(payload: Any) -> CharacterProfile:
    """
    Normalize any raw LLM payload into a valid CharacterProfile immediately.
    """
    if isinstance(payload, CharacterProfile):
        return payload
    data = coerce_json(payload)
    return CharacterProfile.model_validate(data)


# ---------------------------------------------------------------------------
# Node: bootstrap (deterministic synopsis + archetypes)
# ---------------------------------------------------------------------------


async def _bootstrap_node(state: GraphState) -> dict:
    """
    Create/ensure a synopsis and a target list of character archetypes.
    Idempotent: If a synopsis already exists, returns no-op.

    Changes vs legacy:
    - Normalize topic first via planning_tools.normalize_topic (keeps key name 'category').
    - Plan via planning_tools.plan_quiz (compatible InitialPlan model).
    - Build/refine synopsis via content_creation_tools.generate_category_synopsis.
    - ALWAYS derive canonical character list via planning_tools.generate_character_list
      (planner list is used only as fallback). No new state keys are introduced.
    - Validate Pydantic models at node boundary before persisting.
    """
    if state.get("category_synopsis"):
        logger.debug("bootstrap_node.noop", reason="synopsis_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or (
        state["messages"][0].content if state.get("messages") else ""
    )

    logger.info(
        "bootstrap_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category_preview=str(category)[:120],
        env=_env_name(),
    )

    # Normalize topic first (non-breaking: write back to 'category')
    try:
        norm = await tool_normalize_topic.ainvoke({
            "category": category,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        normalized_value = getattr(norm, "category", None) or (
            norm.get("category") if isinstance(norm, dict) else None
        )
        if normalized_value and isinstance(normalized_value, str):
            category = normalized_value.strip() or category
    except Exception as e:
        logger.debug("bootstrap_node.normalize_topic.skipped", reason=str(e))

    # Initial plan (synopsis text + archetype seeds) — via wrapper
    t0 = time.perf_counter()
    try:
        plan: InitialPlan = await tool_plan_quiz.ainvoke({
            "category": category,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        synopsis_text = getattr(plan, "synopsis", "") or ""
        archetype_seeds = getattr(plan, "ideal_archetypes", []) or []
    except Exception as e:
        logger.warning("bootstrap_node.plan_quiz.fail_fallback", error=str(e))
        # Fallback to legacy direct call if wrapper fails
        plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        synopsis_text = getattr(plan, "synopsis", "") or ""
        archetype_seeds = getattr(plan, "ideal_archetypes", []) or []

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "bootstrap_node.initial_plan.ok",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
        synopsis_chars=_safe_len(synopsis_text),
        archetype_count=_safe_len(archetype_seeds),
    )

    # Base synopsis from plan (validated construction)
    synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=synopsis_text)

    # Optional refine via dedicated synopsis generator (wrapper)
    try:
        t1 = time.perf_counter()
        refined = await tool_generate_category_synopsis.ainvoke({
            "category": category,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        refined_synopsis = _validate_synopsis_payload(refined)
        if refined_synopsis and refined_synopsis.summary:
            synopsis_obj = refined_synopsis
        dt1_ms = round((time.perf_counter() - t1) * 1000, 1)
        logger.info(
            "bootstrap_node.synopsis.ready",
            session_id=session_id,
            trace_id=trace_id,
            duration_ms=dt1_ms,
        )
    except Exception as e:
        logger.debug(
            "bootstrap_node.synopsis.refine.skipped",
            session_id=session_id,
            trace_id=trace_id,
            reason=str(e),
        )

    # Derive the canonical/appropriate outcome list via wrapper tool.
    min_chars = getattr(settings.quiz, "min_characters", 3)
    max_chars = getattr(settings.quiz, "max_characters", 6)

    archetypes: List[str] = []
    try:
        generated = await tool_generate_character_list.ainvoke({
            "category": category,
            "synopsis": synopsis_obj.summary,
            "seed_archetypes": archetype_seeds,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        if isinstance(generated, list):
            archetypes = [a for a in generated if isinstance(a, str) and a.strip()]
    except Exception as e:
        logger.debug("bootstrap_node.archetypes.primary.skipped", reason=str(e))

    # Fallback to planner’s suggestions if wrapper returned nothing
    if not archetypes:
        archetypes = list(archetype_seeds)

    # Clamp to max
    archetypes = archetypes[:max_chars]

    # If still short, try wrapper again to pad/merge, preserve order, dedupe
    if len(archetypes) < min_chars:
        try:
            extra = await tool_generate_character_list.ainvoke({
                "category": category,
                "synopsis": synopsis_obj.summary,
                "seed_archetypes": [],
                "trace_id": trace_id,
                "session_id": str(session_id),
            })
            seen = {a.casefold() for a in archetypes}
            for name in extra or []:
                if not isinstance(name, str) or not name.strip():
                    continue
                if name.casefold() in seen:
                    continue
                archetypes.append(name)
                seen.add(name.casefold())
                if len(archetypes) >= min_chars:
                    break
        except Exception as e:
            logger.debug(
                "bootstrap_node.archetypes.expand.skipped",
                session_id=session_id,
                trace_id=trace_id,
                reason=str(e),
            )

    plan_summary = f"Plan for '{category}'. Synopsis ready. Target characters: {archetypes}"

    # Only validated models are written to state:
    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "category_synopsis": synopsis_obj,  # validated Synopsis
        "ideal_archetypes": archetypes,
        "is_error": False,
        "error_message": None,
        "error_count": 0,
    }


# ---------------------------------------------------------------------------
# Node: generate_characters (PARALLEL)
# ---------------------------------------------------------------------------


async def _generate_characters_node(state: GraphState) -> dict:
    """
    Create detailed character profiles for each archetype in PARALLEL.
    Idempotent: If characters already exist, returns no-op.

    Change: Validate each character payload at the node boundary before writing.
    """
    if state.get("generated_characters"):
        logger.debug("characters_node.noop", reason="characters_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category")
    archetypes: List[str] = state.get("ideal_archetypes") or []

    if not archetypes:
        logger.warning(
            "characters_node.no_archetypes", session_id=session_id, trace_id=trace_id
        )
        return {
            "generated_characters": [],
            "messages": [AIMessage(content="No archetypes to generate characters for.")],
        }

    default_concurrency = min(4, max(1, len(archetypes)))
    concurrency = (
        getattr(getattr(settings, "quiz", object()), "character_concurrency", default_concurrency)
        or default_concurrency
    )
    per_call_timeout_s = getattr(getattr(settings, "llm", object()), "per_call_timeout_s", 30)

    logger.info(
        "characters_node.start",
        session_id=session_id,
        trace_id=trace_id,
        target_count=len(archetypes),
        category=category,
        concurrency=concurrency,
        timeout_s=per_call_timeout_s,
    )

    sem = asyncio.Semaphore(concurrency)
    results: List[Optional[CharacterProfile]] = [None] * len(archetypes)

    async def _one(idx: int, name: str) -> None:
        t0 = time.perf_counter()
        try:
            async with sem:
                raw_payload = await asyncio.wait_for(
                    tool_draft_character_profile.ainvoke({
                        "character_name": name,
                        "category": category,
                        "trace_id": trace_id,
                        "session_id": str(session_id),
                    }),
                    timeout=per_call_timeout_s,
                )
            prof = _validate_character_payload(raw_payload)

            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.debug(
                "characters_node.profile.ok",
                session_id=session_id,
                trace_id=trace_id,
                character=name,
                duration_ms=dt_ms,
            )
            results[idx] = prof
        except asyncio.TimeoutError:
            logger.warning(
                "characters_node.profile.timeout",
                session_id=session_id,
                trace_id=trace_id,
                character=name,
                timeout_s=per_call_timeout_s,
            )
        except Exception as e:
            logger.warning(
                "characters_node.profile.fail",
                session_id=session_id,
                trace_id=trace_id,
                character=name,
                error=str(e),
            )

    tasks = [asyncio.create_task(_one(i, name)) for i, name in enumerate(archetypes)]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    characters: List[CharacterProfile] = [c for c in results if c is not None]

    logger.info(
        "characters_node.done",
        session_id=session_id,
        trace_id=trace_id,
        generated_count=len(characters),
        requested=len(archetypes),
    )

    return {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles (parallel).")],
        "generated_characters": characters,  # validated CharacterProfile[]
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: generate_baseline_questions (gated)
# ---------------------------------------------------------------------------


async def _generate_baseline_questions_node(state: GraphState) -> dict:
    """
    Generate the initial set of baseline questions (single structured call).
    Idempotent: If questions already exist, returns no-op.
    PRECONDITION: Router ensures ready_for_questions=True before we get here.
    """
    if state.get("generated_questions"):
        logger.debug("baseline_node.noop", reason="questions_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    synopsis = state.get("category_synopsis")

    logger.info(
        "baseline_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category=category,
        characters=len(characters),
    )

    t0 = time.perf_counter()
    try:
        questions: List[QuizQuestion] = await tool_generate_baseline_questions.ainvoke({
            "category": category,
            "character_profiles": [c.model_dump() for c in characters],
            "synopsis": synopsis.model_dump() if synopsis else {"title": "", "summary": ""},
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
    except Exception as e:
        logger.error("baseline_node.tool_fail", session_id=session_id, trace_id=trace_id, error=str(e), exc_info=True)
        questions = []

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "baseline_node.done",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
        produced=len(questions),
    )

    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions)}")],
        "generated_questions": questions,  # normalized to state shape
        "baseline_count": len(questions),
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: decide / finish / adaptive
# ---------------------------------------------------------------------------


async def _decide_or_finish_node(state: GraphState) -> dict:
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("category_synopsis")
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    history = state.get("quiz_history") or []

    answered = len(history)
    baseline_count = int(state.get("baseline_count") or 0)
    max_q = int(getattr(getattr(settings, "quiz", object()), "max_total_questions", 20))
    min_early = int(getattr(getattr(settings, "quiz", object()), "min_questions_before_early_finish", 6))
    thresh = float(getattr(getattr(settings, "quiz", object()), "early_finish_confidence", 0.9))

    # Must answer all baseline before adaptive
    if answered < baseline_count:
        return {"should_finalize": False, "messages": [AIMessage(content="Awaiting baseline answers")]} 

    # Hard cap: force finish path
    if answered >= max_q:
        action = "FINISH_NOW"; confidence = 1.0; name = ""
    else:
        decision = await tool_decide_next_step.ainvoke({
            "quiz_history": history,
            "character_profiles": [c.model_dump() for c in characters],
            "synopsis": synopsis.model_dump() if synopsis else {"title": "", "summary": ""},
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        # FIX 1: default to the schema-valid "ASK_ONE_MORE_QUESTION"
        action = getattr(decision, "action", "ASK_ONE_MORE_QUESTION")
        confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
        if confidence > 1.0:  # defensively handle % scales
            confidence = min(1.0, confidence / 100.0)
        name = (getattr(decision, "winning_character_name", "") or "").strip()

    if action == "FINISH_NOW" and answered >= min_early and confidence >= thresh:
        # Try the model-provided winner first
        winning = next((c for c in characters if c.name.strip().casefold() == name.casefold()), None)
        # FIX 2: deterministic fallback when forcing finish (e.g., hard cap or missing/mismatched name)
        if not winning and characters:
            winning = characters[0]  # stable order fallback
        if not winning:
            return {"should_finalize": False, "messages": [AIMessage(content="No confident winner; ask one more")]}
        final = await tool_write_final_user_profile.ainvoke({
            "winning_character": winning.model_dump(),
            "quiz_history": history,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        return {"final_result": final, "should_finalize": True, "current_confidence": confidence}
    return {"should_finalize": False}


async def _generate_adaptive_question_node(state: GraphState) -> dict:
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("category_synopsis")
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    existing = state.get("generated_questions") or []

    q = await tool_generate_next_question.ainvoke({
        "quiz_history": history,
        "character_profiles": [c.model_dump() for c in characters],
        "synopsis": synopsis.model_dump() if synopsis else {"title": "", "summary": ""},
        "trace_id": trace_id,
        "session_id": str(session_id),
    })
    return {"generated_questions": [*existing, q]}


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
    syn = state.get("category_synopsis")

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
    have_baseline = bool(state.get("generated_questions"))
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
                        "default_ttl": ttl_minutes,
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
                redis_url=settings.REDIS_URL,
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
        setattr(agent_graph, "_async_checkpointer", checkpointer)
        setattr(agent_graph, "_redis_cm", cm)
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
