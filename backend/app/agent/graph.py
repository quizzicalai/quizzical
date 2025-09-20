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

DB BYPASS:
- Any code that would persist state (characters, sessions, etc.) is commented
  with "DB BYPASS" and left in place for future re-enable.
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

from app.agent.schemas import Synopsis, CharacterProfile, QuizQuestion
from app.agent.state import GraphState
from app.agent.tools.planning_tools import InitialPlan
from app.core.config import settings
from app.services.llm_service import llm_service

# NOTE: Intentionally NOT importing AsyncSession or repositories here.
# from sqlalchemy.ext.asyncio import AsyncSession
# from app.services.database import CharacterRepository, SessionRepository

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


# ---------------------------------------------------------------------------
# Node: bootstrap (deterministic synopsis + archetypes)
# ---------------------------------------------------------------------------


async def _bootstrap_node(state: GraphState) -> dict:
    """
    Create/ensure a synopsis and a target list of character archetypes.
    Idempotent: If a synopsis already exists, returns no-op.
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

    # 1) Initial plan (synopsis + archetypes)
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

    # 2) Build/refine synopsis
    synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=plan.synopsis)
    try:
        t1 = time.perf_counter()
        refined = await llm_service.get_structured_response(
            tool_name="synopsis_generator",
            messages=[HumanMessage(content=category)],
            response_model=Synopsis,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        if refined and refined.summary:
            synopsis_obj = refined
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

    # 3) Clamp archetypes to configured [min,max]; expand if needed
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

    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "category_synopsis": synopsis_obj,
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

    Concurrency control:
    - Uses a semaphore to bound concurrent LLM calls.
    - Optional per-call timeout via asyncio.wait_for.

    DB BYPASS:
    - Persistence of generated characters is intentionally disabled here.
    - See commented block at the end of this function for future re-enable.
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
    # Note: This reads a loose 'llm.per_call_timeout_s' knob if present in YAML; defaults to 30 otherwise.
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
        Stores result in `results[idx]`. Swallows exceptions after logging.
        """
        hint = f"Category: {category}\nCharacter: {name}"
        t0 = time.perf_counter()
        try:
            async with sem:
                prof = await asyncio.wait_for(
                    llm_service.get_structured_response(
                        tool_name="profile_writer",
                        messages=[HumanMessage(content=hint)],
                        response_model=CharacterProfile,
                        session_id=str(session_id),
                        trace_id=trace_id,
                    ),
                    timeout=per_call_timeout_s,
                )
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

    # Launch all tasks in parallel (bounded by semaphore)
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

    # ----------------- DB BYPASS: character persistence (commented) -----------------
    # When re-enabling DB writes, inject an AsyncSession and do:
    #
    # from sqlalchemy.ext.asyncio import AsyncSession
    # from app.services.database import CharacterRepository
    #
    # async def _persist_characters(db_session: AsyncSession, chars: List[CharacterProfile]) -> None:
    #     repo = CharacterRepository(db_session)
    #     for ch in chars:
    #         try:
    #             await repo.create(
    #                 name=ch.name,
    #                 short_description=ch.short_description,
    #                 profile_text=ch.profile_text,
    #                 image_url=ch.image_url,
    #             )
    #         except Exception as e:
    #             logger.warning("characters_node.persist.fail", name=ch.name, error=str(e))
    #
    # await _persist_characters(db_session, characters)
    # ------------------------------------------------------------------------------

    return {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles (parallel).")],
        "generated_characters": characters,
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: generate_baseline_questions (gated)
# ---------------------------------------------------------------------------


class _QOut(BaseModel):
    id: Optional[str] = None
    question_text: str
    # NOTE: Avoid `Any` here; OpenAI rejects schemas where list item lacks a `type`.
    # Use a union that compiles to a valid JSON Schema (`string` or `object`).
    options: List[Union[str, Dict[str, Any]]]


class _QList(BaseModel):
    questions: List[_QOut]


def _normalize_options(raw: List[Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for opt in raw:
        if isinstance(opt, str):
            t = opt.strip()
            if t:
                out.append({"text": t})
        elif isinstance(opt, dict):
            txt = str(opt.get("text") or opt.get("label") or "").strip()
            if txt:
                out.append({"text": txt})
        else:
            s = str(opt).strip()
            if s:
                out.append({"text": s})
    return out


async def _generate_baseline_questions_node(state: GraphState) -> dict:
    """
    Generate the initial set of baseline questions.
    Idempotent: If questions already exist, returns no-op.
    PRECONDITION: The router ensures ready_for_questions=True before we get here.

    DB BYPASS:
    - No session persistence here; state is held by the graph checkpointer/Redis.
    """
    if state.get("generated_questions"):
        logger.debug("baseline_node.noop", reason="questions_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    synopsis: Synopsis = state.get("category_synopsis") or Synopsis(
        title=f"Quiz: {category}", summary=""
    )
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    archetypes: List[str] = state.get("ideal_archetypes") or []

    n = getattr(settings.quiz, "baseline_questions_n", 5)
    m = getattr(settings.quiz, "max_options_m", 4)

    logger.info(
        "baseline_node.start",
        session_id=session_id,
        trace_id=trace_id,
        requested_n=n,
        options_cap_m=m,
        archetypes=len(archetypes),
        characters_available=len(characters),
    )

    # Prefer character blurbs; fall back to archetype names
    if characters:
        char_hint = "\n".join(f"- {c.name}: {c.short_description}" for c in characters)
    else:
        char_hint = "\n".join(f"- {a}" for a in archetypes)

    hint = (
        f"Category: {category}\n"
        f"Synopsis: {synopsis.summary}\n"
        f"Context (characters or archetypes):\n{char_hint}\n\n"
        f"Please create {n} baseline questions appropriate for this quiz."
    )

    t0 = time.perf_counter()
    raw = await llm_service.get_structured_response(
        tool_name="question_generator",
        messages=[HumanMessage(content=hint)],
        response_model=_QList,
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)

    questions: List[QuizQuestion] = []
    for q in raw.questions[:n]:
        opts = _normalize_options(q.options)[:m]
        if not opts:
            opts = [{"text": "Yes"}, {"text": "No"}]
        questions.append(QuizQuestion(question_text=q.question_text, options=opts))

    logger.info(
        "baseline_node.done",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
        produced=len(questions),
    )

    # ----------------- DB BYPASS: session persistence (commented) -----------------
    # When re-enabling DB writes, gather the final state at the sink or in the
    # background runner and call SessionRepository.create_from_agent_state.
    # ------------------------------------------------------------------------------
    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions)}")],
        "generated_questions": questions,
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: assemble_and_finish (sink)
# ---------------------------------------------------------------------------


async def _assemble_and_finish(state: GraphState) -> dict:
    """
    Sink node: logs a compact summary. Safe whether or not questions exist.

    DB BYPASS:
    - We do NOT write the session/final result here. Persistence, if desired,
      should be handled in a background task after graph completion.
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

    # ----------------- DB BYPASS: final session save (commented) -----------------
    # Example for future:
    # async with async_session_factory() as db_session:
    #     repo = SessionRepository(db_session)
    #     await repo.create_from_agent_state(state)
    # ---------------------------------------------------------------------------

    summary = (
        f"Assembly summary → synopsis: {bool(syn)} | "
        f"characters: {len(chars)} | questions: {len(qs)}"
    )
    return {"messages": [AIMessage(content=summary)]}


# ---------------------------------------------------------------------------
# Router / conditionals
# ---------------------------------------------------------------------------


def _should_generate_questions(state: GraphState) -> Literal["questions", "end"]:
    """
    Router after characters: generate questions only if ready_for_questions is True.
    """
    if state.get("ready_for_questions"):
        decision = "questions"
    else:
        decision = "end"
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
    """
    Close the async Redis checkpointer context when present.
    """
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
