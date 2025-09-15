# backend/app/agent/graph.py

"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""
from typing import Literal, Optional, Tuple

import os
import time
import structlog
import redis.asyncio as redis  # kept (even if not directly used) to avoid surprising import diffs
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.redis import RedisSaver  # kept import; not used in async path
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.state import GraphState, Synopsis
from app.agent.tools import get_tools
from app.agent.tools.analysis_tools import analyze_tool_error
from app.agent.tools.planning_tools import InitialPlan
from app.core.config import settings
from app.services.llm_service import llm_service

# --- Logger / helpers ---
logger = structlog.get_logger(__name__)


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


def _is_local_env() -> bool:
    try:
        return (settings.APP_ENVIRONMENT or "local").lower() in {"local", "dev", "development"}
    except Exception:
        return False


# --- Agent Setup ---
tools = get_tools()
logger.info("Agent tools loaded", tool_count=_safe_len(tools))
_tool_runner = ToolNode(tools)
logger.debug("ToolNode initialized", tool_node_id=id(_tool_runner))

# --- Graph Nodes ---

async def agent_node(state: GraphState) -> dict:
    """
    The primary "thinking" node of the agent. It decides which tool to call next.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    messages = state["messages"]

    logger.debug(
        "agent_node invoked",
        session_id=str(session_id),
        trace_id=trace_id,
        message_count=_safe_len(messages),
        is_first_turn=(len(messages) == 1),
        state_keys=_keys(state),
    )

    # On the first turn, the agent creates an initial plan.
    if len(messages) == 1:
        category = messages[0].content
        logger.info(
            "Creating initial plan",
            session_id=str(session_id),
            trace_id=trace_id,
            category=category,
        )
        t0 = time.perf_counter()
        # (Original behavior preserved)
        initial_plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "Initial plan created",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            synopsis_length=_safe_len(initial_plan.synopsis),
            archetype_count=_safe_len(initial_plan.ideal_archetypes),
        )
        plan_summary = (
            f"Plan created for '{category}'. Synopsis: '{initial_plan.synopsis}'. "
            f"Will now find or create characters for the following archetypes: {initial_plan.ideal_archetypes}"
        )
        synopsis_obj = Synopsis(
            title=f"Quiz Synopsis: {category}", summary=initial_plan.synopsis
        )
        logger.debug(
            "agent_node first-turn output prepared",
            session_id=str(session_id),
            trace_id=trace_id,
            has_synopsis=bool(synopsis_obj.summary),
            ideal_archetypes_count=_safe_len(initial_plan.ideal_archetypes),
        )
        return {
            "messages": [AIMessage(content=plan_summary)],
            "category_synopsis": synopsis_obj,
            "ideal_archetypes": initial_plan.ideal_archetypes,
        }

    # For subsequent turns, decide the next tool to call.
    logger.debug(
        "Planning next action",
        session_id=str(session_id),
        trace_id=trace_id,
        last_message_type=type(messages[-1]).__name__ if messages else None,
    )
    t1 = time.perf_counter()
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=messages,
        tools=[t.to_dict() for t in tools],  # keep existing contract with llm_service
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt2_ms = round((time.perf_counter() - t1) * 1000, 1)
    logger.info(
        "Planner response received",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt2_ms,
        has_tool_calls=bool(getattr(response, "tool_calls", None)),
        tool_call_count=_safe_len(getattr(response, "tool_calls", [])),
    )
    return {"messages": [response]}


async def tool_node(state: GraphState) -> dict:
    """
    Executes tools using LangGraph's prebuilt ToolNode. Preserves the original
    is_error / error_message / error_count semantics around the call.
    """
    error_count = state.get("error_count", 0)
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.debug(
        "tool_node invoked",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=error_count,
        last_message_type=type(state["messages"][-1]).__name__ if state.get("messages") else None,
    )
    try:
        t0 = time.perf_counter()
        # ToolNode will read the last AIMessage.tool_calls and return ToolMessages
        new_state = await _tool_runner.ainvoke(state)
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "ToolNode execution complete",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            new_state_keys=_keys(new_state),
        )
        # Ensure flags exist for downstream logic
        if "is_error" not in new_state:
            new_state["is_error"] = False
        if "error_message" not in new_state:
            new_state["error_message"] = None
        new_state["error_count"] = error_count
        logger.debug(
            "tool_node output normalized",
            session_id=str(session_id),
            trace_id=trace_id,
            is_error=new_state.get("is_error"),
            error_message_present=bool(new_state.get("error_message")),
            error_count=new_state.get("error_count"),
        )
        return new_state
    except Exception as e:
        logger.error(
            "Tool execution failed",
            session_id=str(session_id),
            trace_id=trace_id,
            error=str(e),
            exc_info=True,
        )
        # Surface any tool execution failure into the existing error flow
        return {
            "messages": [],
            "is_error": True,
            "error_message": f"Tool execution failed: {e}",
            "error_count": error_count + 1,
        }


async def error_node(state: GraphState) -> dict:
    """
    Handles errors by analyzing them and preparing for a retry.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.warning(
        "error_node invoked",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=state.get("error_count"),
        error_message=state.get("error_message"),
    )
    t0 = time.perf_counter()
    corrective_action = await analyze_tool_error.ainvoke(
        {"error_message": state["error_message"], "state": dict(state)}
    )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "Error analyzed",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        corrective_action_summary=str(corrective_action)[:200] if corrective_action is not None else None,
    )

    error_summary = (
        f"Attempt {state['error_count']}: An error occurred. "
        f"Analysis: {corrective_action}. Retrying..."
    )

    logger.debug(
        "error_node output prepared",
        session_id=str(session_id),
        trace_id=trace_id,
        will_retry=True,
    )
    return {
        "messages": [AIMessage(content=error_summary)],
        "is_error": False,  # Reset the error flag for the next attempt
    }


# --- Conditional Edges ---

def should_continue(state: GraphState) -> Literal["tools", "end"]:
    """Determines whether to call tools or end the process."""
    decision: Literal["tools", "end"]
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        decision = "tools"
    else:
        decision = "end"
    logger.debug(
        "should_continue decision",
        has_tool_calls=(decision == "tools"),
        decision=decision,
    )
    return decision


def after_tools(state: GraphState) -> Literal["agent", "error", "end"]:
    """
    Checks for tool execution errors and decides the next step.
    """
    decision: Literal["agent", "error", "end"]
    if state.get("is_error"):
        if state.get("error_count", 0) >= settings.agent.max_retries:
            decision = "end"
        else:
            decision = "error"
    else:
        decision = "agent"
    logger.debug(
        "after_tools decision",
        is_error=state.get("is_error"),
        error_count=state.get("error_count"),
        max_retries=settings.agent.max_retries,
        decision=decision,
    )
    return decision


# --- Graph Definition ---
workflow = StateGraph(GraphState)
logger.debug("StateGraph initialized", workflow_id=id(workflow))

workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.add_node("error", error_node)
logger.debug("Graph nodes added", nodes=["agent", "tools", "error"])

workflow.set_entry_point("agent")
logger.debug("Entry point set", entry_point="agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", "end": END},
)
logger.debug("Conditional edges set for 'agent'")

workflow.add_conditional_edges(
    "tools",
    after_tools,
    {"agent": "agent", "error": "error", "end": END},
)
logger.debug("Conditional edges set for 'tools'")

# After an error is analyzed, loop back to the agent to retry.
workflow.add_edge("error", "agent")
logger.debug("Edge added", source="error", target="agent")


# --- Graph Compilation / Checkpointer Factory ---

async def create_agent_graph():
    """
    Factory function to create and compile the agent graph with its checkpointer.

    NOTE: Async so we can initialize the async Redis checkpointer.
    Returns a compiled graph; the underlying checkpointer is attached to
    the compiled object as `_async_checkpointer` for clean shutdown.
    """
    logger.info("Creating agent graph (with Redis checkpointer)")
    t0 = time.perf_counter()

    env = (settings.APP_ENVIRONMENT or "local").lower()
    use_memory_saver = os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}

    checkpointer = None

    # TTL configuration (minutes); keep optional so we don't require config changes.
    # Default to modest expiration to avoid unbounded growth; can be overridden via env.
    ttl_env = os.getenv("LANGGRAPH_REDIS_TTL_MIN", "").strip()
    ttl_minutes: Optional[int] = None
    if ttl_env.isdigit():
        try:
            ttl_minutes = max(1, int(ttl_env))
        except Exception:
            ttl_minutes = None

    ttl_cfg = None
    if ttl_minutes:
        ttl_cfg = {
            "default_ttl": ttl_minutes,     # minutes
            "refresh_on_read": True,        # reset TTL when reading checkpoints
        }

    # Prefer MemorySaver in local/dev if explicitly requested
    if env in {"local", "dev", "development"} and use_memory_saver:
        checkpointer = MemorySaver()
        logger.info(
            "Using MemorySaver for local development",
            env=env,
            use_memory_saver=use_memory_saver,
        )
    else:
        # Use documented async factory to construct the Redis checkpointer
        try:
            # NOTE: AsyncRedisSaver builds its own client (bytes I/O), independent of our app pool.
            # This matches the library guidance and avoids decode_responses mismatches.
            if ttl_cfg:
                checkpointer = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_cfg)
                logger.debug(
                    "AsyncRedisSaver created via from_conn_string with TTL",
                    redis_url=settings.REDIS_URL,
                    ttl_minutes=ttl_cfg.get("default_ttl"),
                    refresh_on_read=ttl_cfg.get("refresh_on_read"),
                )
            else:
                checkpointer = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
                logger.debug(
                    "AsyncRedisSaver created via from_conn_string (no TTL)",
                    redis_url=settings.REDIS_URL,
                )

            # Initialize indices / structures (required by implementation)
            await checkpointer.asetup()
            logger.info(
                "AsyncRedisSaver setup complete",
                redis_url=settings.REDIS_URL,
                ttl_minutes=ttl_cfg.get("default_ttl") if ttl_cfg else None,
            )
        except Exception as e:
            # Common causes: missing RedisJSON/RediSearch modules or connectivity
            logger.warning(
                "Failed to initialize AsyncRedisSaver, falling back to MemorySaver",
                error=str(e),
                env=env,
                hint="Ensure RedisJSON & RediSearch modules are available or use Redis 8+ / Redis Stack.",
            )
            checkpointer = MemorySaver()

    # Capability snapshot for debugging
    logger.info(
        "Checkpointer capabilities",
        checkpointer_class=type(checkpointer).__name__,
        has_aget_tuple=hasattr(checkpointer, "aget_tuple"),
        has_get_tuple=hasattr(checkpointer, "get_tuple"),
        has_aput=hasattr(checkpointer, "aput"),
        implements_async=callable(getattr(checkpointer, "aget_tuple", None)),
    )

    # Compile the graph with the chosen checkpointer
    agent_graph = workflow.compile(checkpointer=checkpointer)

    # Attach the checkpointer so the lifespan hook can close it explicitly.
    try:
        setattr(agent_graph, "_async_checkpointer", checkpointer)
    except Exception:
        # Non-fatal; compilation succeeded already.
        logger.debug("Could not attach checkpointer to compiled graph")

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "Agent graph compiled",
        duration_ms=dt_ms,
        agent_graph_type=type(agent_graph).__name__,
        agent_graph_id=id(agent_graph),
    )
    return agent_graph


async def aclose_agent_graph(agent_graph) -> None:
    """
    Helper to explicitly close the async Redis checkpointer if one is attached.

    NOTE: This doesn't change existing call sites. If the caller uses this helper
    from the shutdown path, it guarantees a clean disconnect of the checkpointer.
    """
    cp = getattr(agent_graph, "_async_checkpointer", None)
    if hasattr(cp, "aclose"):
        try:
            await cp.aclose()
            logger.info("AsyncRedisSaver closed")
        except Exception as e:
            logger.warning("Failed to close AsyncRedisSaver", error=str(e), exc_info=True)
