# backend/app/agent/graph.py
"""
Main Agent Graph (synopsis/characters first → gated questions)

This LangGraph builds a quiz in two phases:

1) User-facing preparation
   - bootstrap → deterministic synopsis + archetype list
   - generate_characters → detailed character profiles (NOW PARALLEL)
   These run during /quiz/start. The request returns once synopsis (and
   typically characters) are ready.

2) Gated question generation
   - Only when the client calls /quiz/proceed, the API flips a state flag
     `ready_for_questions=True`. On the next graph run, a router sends flow
     to `generate_baseline_questions`, then to the sink.

Design notes:
- Nodes are idempotent: re-running after END will not redo work that exists.
- Removes legacy planner/tools loop and any `.to_dict()` tool usage.
- Uses async Redis checkpointer per langgraph-checkpoint-redis v0.1.x guidance.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional, Literal, Union

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, create_model

# Import canonical models from agent.state (re-exported from schemas)
from app.agent.state import GraphState, Synopsis, CharacterProfile, QuizQuestion
from app.agent.tools.planning_tools import InitialPlan
from app.agent.tools.content_creation_tools import (  # <-- use strengthened tool for questions
    generate_baseline_questions as tool_generate_baseline_questions,
    generate_next_question as tool_generate_next_question,
    decide_next_step as tool_decide_next_step,
    write_final_user_profile as tool_write_final_user_profile,
)
# NEW: planning helpers (non-breaking: keep key as `category`)
from app.agent.tools.planning_tools import (
    normalize_topic as tool_normalize_topic,
    plan_quiz as tool_plan_quiz,
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
    This prevents leaking dicts/strings into state/Redis.
    """
    if isinstance(payload, Synopsis):
        return payload

    data = coerce_json(payload)
    # Legacy guard (defensive): map synopsis_text -> summary if needed.
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

    Change: Validate the synopsis at the node boundary before writing to state.
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
        category=category,
        env=_env_name(),
    )

    # NEW: Normalize topic first (but keep writing to `category` to avoid breaking changes)
    try:
        norm = await tool_normalize_topic.ainvoke({
            "category": category,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        normalized_value = getattr(norm, "category", None) or (norm.get("category") if isinstance(norm, dict) else None)
        if normalized_value and isinstance(normalized_value, str):
            category = normalized_value.strip() or category
    except Exception as e:
        logger.debug("bootstrap_node.normalize_topic.skipped", reason=str(e))

    # 1) Initial plan (synopsis + archetypes) — now via planning tool (non-breaking shape)
    t0 = time.perf_counter()
    try:
        plan: InitialPlan = await tool_plan_quiz.ainvoke({
            "category": category,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
        # guard for objects/dicts
        plan_synopsis = getattr(plan, "synopsis", None) or (plan.get("synopsis") if isinstance(plan, dict) else "")
        plan_archetypes = getattr(plan, "ideal_archetypes", None) or (plan.get("ideal_archetypes") if isinstance(plan, dict) else [])
        # construct a shallow InitialPlan-like shim for logging below
        class _Shim:
            synopsis = plan_synopsis
            ideal_archetypes = plan_archetypes
        plan = _Shim()  # type: ignore[assignment]
    except Exception as e:
        logger.warning("bootstrap_node.plan_quiz.fail_fallback", error=str(e))
        # Fallback to legacy direct call if planning tool fails
        plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "bootstrap_node.initial_plan.ok",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
        synopsis_chars=_safe_len(getattr(plan, "synopsis", "")),
        archetype_count=_safe_len(getattr(plan, "ideal_archetypes", [])),
    )

    # 2) Base synopsis from plan (safe, validated construction)
    synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=getattr(plan, "synopsis", "") or "")

    # 3) Optional refine via dedicated synopsis generator
    #    VALIDATE AT NODE BOUNDARY before writing into state/Redis.
    try:
        t1 = time.perf_counter()
        refined_payload = await llm_service.get_structured_response(
            tool_name="synopsis_generator",
            messages=[HumanMessage(content=category)],
            # This call *should* return a Synopsis, but we still validate defensively.
            response_model=Synopsis,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        # Validate/coerce regardless of what the service returned (model/dict/str)
        refined_synopsis = _validate_synopsis_payload(refined_payload)
        # Prefer refined if it actually carries a summary
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

    # 4) Clamp archetypes to configured [min,max]; expand if needed
    min_chars = getattr(settings.quiz, "min_characters", 3)
    max_chars = getattr(settings.quiz, "max_characters", 6)
    archetypes: List[str] = (getattr(plan, "ideal_archetypes", []) or [])[:max_chars]

    if len(archetypes) < min_chars:
        try:
            msg = (
                f"Category: {category}\nSynopsis: {synopsis_obj.summary}\n"
                f"Need at least {min_chars} distinct archetypes."
            )
            # Create a valid Pydantic v2 model dynamically
            ArchetypesOut = create_model(
                "ArchetypesOut",
                archetypes=(List[str], Field(...)),
            )
            extra = await llm_service.get_structured_response(
                tool_name="character_list_generator",
                messages=[HumanMessage(content=msg)],
                response_model=ArchetypesOut,  # type: ignore[arg-type]
                session_id=str(session_id),
                trace_id=trace_id,
            )
            for name in getattr(extra, "archetypes", []):
                if len(archetypes) >= min_chars:
                    break
                if name not in archetypes:
                    archetypes.append(name)
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
# Node: generate_characters (NOW PARALLEL)
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

    # Concurrency/timing knobs with safe defaults
    default_concurrency = min(4, max(1, len(archetypes)))
    concurrency = getattr(getattr(settings, "quiz", object()), "character_concurrency", default_concurrency) or default_concurrency
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
        """
        Generate one character with timeout and structured response.
        Stores validated result in `results[idx]`. Swallows exceptions after logging.
        """
        hint = f"Category: {category}\nCharacter: {name}"
        t0 = time.perf_counter()
        try:
            async with sem:
                raw_payload = await asyncio.wait_for(
                    llm_service.get_structured_response(
                        tool_name="profile_writer",
                        messages=[HumanMessage(content=hint)],
                        # Service should already return CharacterProfile, but validate anyway:
                        response_model=CharacterProfile,
                        session_id=str(session_id),
                        trace_id=trace_id,
                    ),
                    timeout=per_call_timeout_s,
                )
            # STRICT NODE-BOUNDARY VALIDATION:
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

    # Filter out None, keep original order
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
    Generate the initial set of baseline questions.
    Idempotent: If questions already exist, returns no-op.
    PRECONDITION: The router ensures ready_for_questions=True before we get here.

    CHANGE (surgical):
    - Remove ad-hoc schema/normalizer here.
    - Delegate to the strengthened tool `generate_baseline_questions`, which:
        * runs bounded parallel structured calls,
        * enforces max options, and
        * returns already-normalized `List[QuizQuestion]`.
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
        "generated_questions": questions,  # already normalized to state shape
        "baseline_count": len(questions),
        "is_error": False,
        "error_message": None,
    }


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
        action = decision.action
        confidence = float(decision.confidence or 0.0)
        if confidence > 1.0:
            confidence = min(1.0, confidence / 100.0)
        name = (decision.winning_character_name or "").strip()

    if action == "FINISH_NOW" and answered >= min_early and confidence >= thresh:
        winning = next((c for c in characters if c.name.strip().casefold() == name.casefold()), None)
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

    ttl_cfg = {"default_ttl": ttl_minutes, "refresh_on_read": True} if ttl_minutes else None

    if env in {"local", "dev", "development"} and use_memory_saver:
        checkpointer = MemorySaver()
        logger.info("graph.checkpointer.memory", env=env)
    else:
        try:
            cm = (
                AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_cfg)
                if ttl_cfg
                else AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
            )
            checkpointer = await cm.__aenter__()
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
