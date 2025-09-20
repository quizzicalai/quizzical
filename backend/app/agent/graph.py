# backend/app/agent/graph.py
"""
Main Agent Graph (Azure/YAML-config aware)

This module defines the LangGraph agent that orchestrates the quiz-generation
workflow. It now supports two entry modes:

- **Agent-first (legacy / production)**: Entry at "agent" (dynamic planner + tools).
- **Bootstrap-first (local)**: Entry at "bootstrap" to deterministically create the
  synopsis, characters, and baseline questions before handing off to the agent loop.

Selection is driven by: settings.feature_flags.flow_mode == "local" → bootstrap-first.

Redis checkpointer wiring follows langgraph-checkpoint-redis v0.1.1 guidance:
- Create the async context manager with AsyncRedisSaver.from_conn_string(...)
- Enter it to obtain the saver, then call await saver.asetup()
- Keep the context manager alive for app lifetime; close via __aexit__ at shutdown
"""

from __future__ import annotations

from typing import Literal, Optional, List, Dict, Any
import os
import time

import structlog
import redis.asyncio as redis  # retained for dependency expectations / potential future use
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis import RedisSaver  # retained import (not used directly)
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from pydantic import BaseModel

from app.agent.state import GraphState, Synopsis, CharacterProfile, QuizQuestion
from app.agent.tools import get_tools
from app.agent.tools.planning_tools import InitialPlan
from app.agent.tools.analysis_tools import analyze_tool_error
from app.core.config import settings
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_len(obj):
    try:
        return len(obj)  # type: ignore[arg-type]
    except Exception:
        return None


def _keys(obj):
    try:
        return list(obj.keys())  # type: ignore[assignment]
    except Exception:
        return None


def _env_name() -> str:
    try:
        return (settings.app.environment or "local").lower()
    except Exception:
        return "local"


def _should_use_local_bootstrap() -> bool:
    """
    Feature-flagged local flow. When 'local', we start at 'bootstrap'
    to deterministically create synopsis/characters/baseline questions.
    """
    try:
        return (settings.feature_flags.flow_mode or "agent").lower() == "local"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# New bootstrap nodes (deterministic, config-driven)
# ---------------------------------------------------------------------------

async def _bootstrap_node(state: GraphState) -> dict:
    """
    Deterministically generate the quiz synopsis and the target character archetypes.
    - Uses legacy 'initial_planner' for robust archetype extraction.
    - Optionally refines synopsis with 'synopsis_generator' (if configured).
    - Enforces max character count (truncation); best-effort on min.
    - Leaves detailed character profile generation to the next node.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or (state["messages"][0].content if state.get("messages") else "")
    logger.info(
        "bootstrap_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        category=category,
        env=_env_name(),
    )

    # 1) Initial plan
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
        session_id=str(session_id),
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
        dt1_ms = round((time.perf_counter() - t1) * 1000, 1)
        if refined and refined.summary:
            synopsis_obj = refined
            logger.info(
                "bootstrap_node.synopsis.refined",
                session_id=str(session_id),
                trace_id=trace_id,
                duration_ms=dt1_ms,
            )
    except Exception as e:
        logger.debug(
            "bootstrap_node.synopsis.refine.skipped",
            session_id=str(session_id),
            trace_id=trace_id,
            reason=str(e),
        )

    # 3) Clamp archetypes to [min,max]
    min_chars = settings.quiz.min_characters
    max_chars = settings.quiz.max_characters
    archetypes: List[str] = plan.ideal_archetypes or []

    if len(archetypes) > max_chars:
        archetypes = archetypes[:max_chars]
        logger.debug(
            "bootstrap_node.archetypes.truncated",
            session_id=str(session_id),
            trace_id=trace_id,
            max_chars=max_chars,
        )

    if len(archetypes) < min_chars:
        try:
            t2 = time.perf_counter()
            msg = f"Category: {category}\nSynopsis: {synopsis_obj.summary}"
            extra = await llm_service.get_structured_response(
                tool_name="character_list_generator",
                messages=[HumanMessage(content=msg)],
                response_model=type(
                    "ArchetypesOut",
                    (BaseModel,),
                    {"archetypes": (List[str], ...)},
                ),
                session_id=str(session_id),
                trace_id=trace_id,
            )
            dt2 = round((time.perf_counter() - t2) * 1000, 1)
            logger.info(
                "bootstrap_node.archetypes.expand.attempt",
                session_id=str(session_id),
                trace_id=trace_id,
                duration_ms=dt2,
                returned=_safe_len(getattr(extra, "archetypes", [])),
            )
            for name in getattr(extra, "archetypes", []):
                if len(archetypes) >= min_chars:
                    break
                if name not in archetypes:
                    archetypes.append(name)
        except Exception as e:
            logger.debug(
                "bootstrap_node.archetypes.expand.skipped",
                session_id=str(session_id),
                trace_id=trace_id,
                reason=str(e),
            )

    if len(archetypes) > max_chars:
        archetypes = archetypes[:max_chars]

    logger.info(
        "bootstrap_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        synopsis_len=_safe_len(synopsis_obj.summary),
        archetype_count=len(archetypes),
        min_chars=min_chars,
        max_chars=max_chars,
    )

    plan_summary = (
        f"Plan for '{category}'. Synopsis ready. "
        f"Target characters to create: {archetypes}"
    )

    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "category_synopsis": synopsis_obj,
        "ideal_archetypes": archetypes,
        "is_error": False,
        "error_message": None,
    }


async def _generate_characters_node(state: GraphState) -> dict:
    """
    Create detailed character profiles for each target archetype using 'profile_writer'.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category")
    archetypes: List[str] = state.get("ideal_archetypes") or []

    if not archetypes:
        return {
            "messages": [AIMessage(content="No archetypes available to generate characters.")],
            "generated_characters": [],
        }

    logger.info(
        "characters_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        target_count=len(archetypes),
        category=category,
    )

    characters: List[CharacterProfile] = []

    for name in archetypes:
        try:
            hint = f"Category: {category}\nCharacter: {name}"
            t0 = time.perf_counter()
            prof = await llm_service.get_structured_response(
                tool_name="profile_writer",
                messages=[HumanMessage(content=hint)],
                response_model=CharacterProfile,
                session_id=str(session_id),
                trace_id=trace_id,
            )
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.debug(
                "characters_node.profile.ok",
                session_id=str(session_id),
                trace_id=trace_id,
                character=name,
                duration_ms=dt_ms,
            )
            characters.append(prof)
        except Exception as e:
            logger.warning(
                "characters_node.profile.fail",
                session_id=str(session_id),
                trace_id=trace_id,
                character=name,
                error=str(e),
            )

    logger.info(
        "characters_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        generated_count=len(characters),
    )

    return {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles.")],
        "generated_characters": characters,
        "is_error": False,
        "error_message": None,
    }


# Simple structured model for baseline question parsing
class _QOut(BaseModel):
    id: Optional[str] = None
    question_text: str
    options: List[Any]  # strings or dicts with 'text'/'label'


class _QList(BaseModel):
    questions: List[_QOut]


def _normalize_options(raw: List[Any]) -> List[Dict[str, str]]:
    """Normalize options to [{'text': '...'}]"""
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
    Generate baseline questions (n) with up to m options per question.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    characters: List[CharacterProfile] = state.get("generated_characters") or []

    n = settings.quiz.baseline_questions_n
    m = settings.quiz.max_options_m

    logger.info(
        "baseline_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        requested_n=n,
        options_cap_m=m,
        character_count=len(characters),
    )

    char_hint = "\n".join(f"- {c.name}: {c.short_description}" for c in characters[: settings.quiz.max_characters])
    hint = f"Category: {category}\nCharacters:\n{char_hint}\nPlease create {n} baseline questions."

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
    for idx, q in enumerate(raw.questions[: n]):
        opts = _normalize_options(q.options)[:m]
        if not opts:
            opts = [{"text": "Yes"}, {"text": "No"}]
        questions.append(QuizQuestion(question_text=q.question_text, options=opts))

    logger.info(
        "baseline_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        produced=len(questions),
    )

    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions)}")],
        "generated_questions": questions,
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Legacy agent planner + tool loop
# ---------------------------------------------------------------------------

tools = get_tools()
logger.info("Agent tools loaded", tool_count=_safe_len(tools))
_tool_runner = ToolNode(tools)
logger.debug("ToolNode initialized", tool_node_id=id(_tool_runner))


async def agent_node(state: GraphState) -> dict:
    """
    Legacy/dynamic planner node. If first call (no synopsis yet), create InitialPlan.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    messages = state["messages"]

    logger.debug(
        "agent_node",
        session_id=str(session_id),
        trace_id=trace_id,
        message_count=_safe_len(messages),
    )

    if len(messages) == 1 and not state.get("category_synopsis"):
        category = state["messages"][0].content
        logger.info("agent_node.initial_plan", session_id=str(session_id), trace_id=trace_id, category=category)
        t0 = time.perf_counter()
        initial_plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=initial_plan.synopsis)
        logger.info(
            "agent_node.initial_plan.ok",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            archetype_count=_safe_len(initial_plan.ideal_archetypes),
        )
        plan_summary = (
            f"Plan created for '{category}'. Synopsis ready. "
            f"Will now create characters: {initial_plan.ideal_archetypes}"
        )
        return {
            "messages": [AIMessage(content=plan_summary)],
            "category": category,
            "category_synopsis": synopsis_obj,
            "ideal_archetypes": initial_plan.ideal_archetypes,
        }

    logger.debug("agent_node.plan_next", session_id=str(session_id), trace_id=trace_id)
    t1 = time.perf_counter()
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=messages,
        tools=[t.to_dict() for t in tools],
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt2_ms = round((time.perf_counter() - t1) * 1000, 1)
    logger.info(
        "agent_node.planner.ok",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt2_ms,
        has_tool_calls=bool(getattr(response, "tool_calls", None)),
        tool_call_count=_safe_len(getattr(response, "tool_calls", [])),
    )
    return {"messages": [response]}


async def tool_node(state: GraphState) -> dict:
    """
    Executes tools via ToolNode and normalizes error flags in state.
    """
    error_count = state.get("error_count", 0)
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.debug(
        "tool_node",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=error_count,
        last_message_type=type(state["messages"][-1]).__name__ if state.get("messages") else None,
    )
    try:
        t0 = time.perf_counter()
        new_state = await _tool_runner.ainvoke(state)
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "tool_node.ok",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            new_state_keys=_keys(new_state),
        )
        new_state.setdefault("is_error", False)
        new_state.setdefault("error_message", None)
        new_state["error_count"] = error_count
        return new_state
    except Exception as e:
        logger.error(
            "tool_node.fail",
            session_id=str(session_id),
            trace_id=trace_id,
            error=str(e),
            exc_info=True,
        )
        return {"messages": [], "is_error": True, "error_message": f"Tool execution failed: {e}", "error_count": error_count + 1}


async def error_node(state: GraphState) -> dict:
    """
    Analyze last error and prepare a retry message. Retries bounded by settings.agent.max_retries.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.warning(
        "error_node",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=state.get("error_count"),
        error_message=state.get("error_message"),
    )
    t0 = time.perf_counter()
    corrective_action = await analyze_tool_error.ainvoke({"error_message": state.get("error_message"), "state": dict(state)})
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "error_node.analysis",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        corrective=str(corrective_action)[:200] if corrective_action is not None else None,
    )
    return {"messages": [AIMessage(content=f"Retrying after error: {corrective_action}")], "is_error": False}


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------

workflow = StateGraph(GraphState)
logger.debug("graph.init", workflow_id=id(workflow))

# Deterministic bootstrap chain
workflow.add_node("bootstrap", _bootstrap_node)
workflow.add_node("generate_characters", _generate_characters_node)
workflow.add_node("generate_baseline_questions", _generate_baseline_questions_node)
logger.debug("graph.nodes.added.bootstrap", nodes=["bootstrap", "generate_characters", "generate_baseline_questions"])

# Legacy planner + tools + error
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.add_node("error", error_node)
logger.debug("graph.nodes.added.legacy", nodes=["agent", "tools", "error"])

# Default entry → overridden at compile time based on feature flag
workflow.set_entry_point("agent")
logger.debug("graph.entry.set", entry_point="agent")

# Bootstrap chain: bootstrap → generate_characters → generate_baseline_questions → agent
workflow.add_edge("bootstrap", "generate_characters")
workflow.add_edge("generate_characters", "generate_baseline_questions")
workflow.add_edge("generate_baseline_questions", "agent")
logger.debug("graph.edges.bootstrap", edges=[("bootstrap","generate_characters"),("generate_characters","generate_baseline_questions"),("generate_baseline_questions","agent")])

# Planner conditionals
def should_continue(state: GraphState) -> Literal["tools", "end"]:
    last = state["messages"][-1]
    decision: Literal["tools", "end"] = "tools" if isinstance(last, AIMessage) and getattr(last, "tool_calls", None) else "end"
    logger.debug("edge.should_continue", decision=decision)
    return decision


def after_tools(state: GraphState) -> Literal["agent", "error", "end"]:
    if state.get("is_error"):
        decision: Literal["agent", "error", "end"] = "end" if state.get("error_count", 0) >= settings.agent.max_retries else "error"
    else:
        decision = "agent"
    logger.debug("edge.after_tools", decision=decision, error=state.get("is_error"), error_count=state.get("error_count"))
    return decision


workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
workflow.add_conditional_edges("tools", after_tools, {"agent": "agent", "error": "error", "end": END})
workflow.add_edge("error", "agent")


# ---------------------------------------------------------------------------
# Checkpointer factory (v0.1.1-compatible async Redis wiring)
# ---------------------------------------------------------------------------

async def create_agent_graph():
    """
    Compile the graph with a checkpointer.

    Redis saver (langgraph-checkpoint-redis v0.1.1) must be *entered* as an async
    context manager to obtain the saver object, then `await saver.asetup()` must be
    called before use. We keep the context manager alive on the compiled graph and
    close it at application shutdown via `aclose_agent_graph`.

    Reference: redis-developer/langgraph-redis README (Async Implementation).  # noqa
    """
    logger.info("graph.compile.start", env=_env_name())
    t0 = time.perf_counter()

    env = _env_name()
    use_memory_saver = os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}

    # Choose entry point based on feature flag (no prod behavior change)
    if _should_use_local_bootstrap():
        workflow.set_entry_point("bootstrap")
        logger.info("graph.entry.override", entry_point="bootstrap")
    else:
        workflow.set_entry_point("agent")
        logger.info("graph.entry.override", entry_point="agent")

    checkpointer = None
    cm = None  # async context manager holder

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
            # Build the async context manager
            if ttl_cfg:
                cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_cfg)
            else:
                cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)

            # Enter context manager to obtain saver, then setup indices
            checkpointer = await cm.__aenter__()
            await checkpointer.asetup()  # per official examples

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
            cm = None  # nothing to close

    agent_graph = workflow.compile(checkpointer=checkpointer)

    # Attach for explicit shutdown
    try:
        setattr(agent_graph, "_async_checkpointer", checkpointer)
        setattr(agent_graph, "_redis_cm", cm)
    except Exception:
        logger.debug("graph.checkpointer.attach.skip")

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "graph.compile.done",
        duration_ms=dt_ms,
        entry_point=("bootstrap" if _should_use_local_bootstrap() else "agent"),
    )
    return agent_graph


async def aclose_agent_graph(agent_graph) -> None:
    """
    Close the async Redis checkpointer context when present.

    If we created an AsyncRedisSaver via from_conn_string(), we manually entered
    its context in create_agent_graph() and must __aexit__ it here. If the saver
    exposes `aclose()`, we call that defensively first.
    """
    cm = getattr(agent_graph, "_redis_cm", None)
    cp = getattr(agent_graph, "_async_checkpointer", None)

    # Try per-saver close if available
    if hasattr(cp, "aclose"):
        try:
            await cp.aclose()
            logger.info("graph.checkpointer.redis.aclose.ok")
        except Exception as e:
            logger.warning("graph.checkpointer.redis.aclose.fail", error=str(e), exc_info=True)

    # Ensure the context manager is properly exited
    if cm is not None and hasattr(cm, "__aexit__"):
        try:
            await cm.__aexit__(None, None, None)
            logger.info("graph.checkpointer.redis.closed")
        except Exception as e:
            logger.warning("graph.checkpointer.redis.close.fail", error=str(e), exc_info=True)
