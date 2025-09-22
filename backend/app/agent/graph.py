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

    # 1) Initial plan (synopsis + archetypes) — already structured/validated via Pydantic
    t0 = time.perf_counter()
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
        synopsis_chars=_safe_len(plan.synopsis),
        archetype_count=_safe_len(plan.ideal_archetypes),
    )

    # 2) Base synopsis from plan (safe, validated construction)
    synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=plan.synopsis)

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
    archetypes: List[str] = (plan.ideal_archetypes or [])[:max_chars]

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

    logger.info(
        "baseline_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category=category,
        characters=len(characters),
    )

    t0 = time.perf_counter()
    try:
        questions: List[QuizQuestion] = await tool_generate_baseline_questions.ainvoke(
            {
                "category": category,
                "character_profiles": [c.model_dump() for c in characters],
                "trace_id": trace_id,
                "session_id": str(session_id),
            }
        )
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
        "is_error": False,
        "error_message": None,
    }


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


def _should_generate_questions(state: GraphState) -> Literal["questions", "end"]:
    """Router after characters: generate questions only if ready_for_questions is True."""
    decision = "questions" if state.get("ready_for_questions") else "end"
    logger.debug(
        "router.after_characters",
        ready_for_questions=bool(state.get("ready_for_questions")),
        decision=decision,
    )
    return decision


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------


workflow = StateGraph(GraphState)
logger.debug("graph.init", workflow_id=id(workflow))

# Nodes
workflow.add_node("bootstrap", _bootstrap_node)
workflow.add_node("generate_characters", _generate_characters_node)
workflow.add_node("generate_baseline_questions", _generate_baseline_questions_node)
workflow.add_node("assemble_and_finish", _assemble_and_finish)

# Entry
workflow.set_entry_point("bootstrap")

# Linear prep: bootstrap → generate_characters
workflow.add_edge("bootstrap", "generate_characters")

# Router: characters → (questions | END)
workflow.add_conditional_edges(
    "generate_characters",
    _should_generate_questions,
    {
        "questions": "generate_baseline_questions",
        "end": END,
    },
)

# If questions were generated, fan into sink then end
workflow.add_edge("generate_baseline_questions", "assemble_and_finish")
workflow.add_edge("assemble_and_finish", END)

logger.debug(
    "graph.wired",
    edges=[
        ("bootstrap", "generate_characters"),
        ("generate_characters", "generate_baseline_questions/END via router"),
        ("generate_baseline_questions", "assemble_and_finish"),
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
