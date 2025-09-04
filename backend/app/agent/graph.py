# backend/app/agent/graph.py

"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode  # UPDATED: replace ToolExecutor

from app.agent.state import GraphState, Synopsis
from app.agent.tools import get_tools
from app.agent.tools.analysis_tools import analyze_tool_error
from app.agent.tools.planning_tools import InitialPlan
from app.core.config import settings
from app.services.llm_service import llm_service

# --- Agent Setup ---
tools = get_tools()
# UPDATED: use prebuilt ToolNode
_tool_runner = ToolNode(tools)


# --- Graph Nodes ---

async def agent_node(state: GraphState) -> dict:
    """
    The primary "thinking" node of the agent. It decides which tool to call next.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    messages = state["messages"]

    # On the first turn, the agent creates an initial plan.
    if len(messages) == 1:
        category = messages[0].content
        initial_plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        plan_summary = (
            f"Plan created for '{category}'. Synopsis: '{initial_plan.synopsis}'. "
            f"Will now find or create characters for the following archetypes: {initial_plan.ideal_archetypes}"
        )
        synopsis_obj = Synopsis(
            title=f"Quiz Synopsis: {category}", summary=initial_plan.synopsis
        )
        return {
            "messages": [AIMessage(content=plan_summary)],
            "category_synopsis": synopsis_obj,
            "ideal_archetypes": initial_plan.ideal_archetypes,
        }

    # For subsequent turns, decide the next tool to call.
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=messages,
        tools=[t.to_dict() for t in tools],  # keep existing contract with llm_service
        session_id=str(session_id),
        trace_id=trace_id,
    )
    return {"messages": [response]}


# UPDATED: delegate tool execution to ToolNode and preserve error flags
async def tool_node(state: GraphState) -> dict:
    """
    Executes tools using LangGraph's prebuilt ToolNode. Preserves the original
    is_error / error_message / error_count semantics around the call.
    """
    error_count = state.get("error_count", 0)
    try:
        # ToolNode will read the last AIMessage.tool_calls and return ToolMessages
        new_state = await _tool_runner.ainvoke(state)
        # Ensure flags exist for downstream logic
        if "is_error" not in new_state:
            new_state["is_error"] = False
        if "error_message" not in new_state:
            new_state["error_message"] = None
        new_state["error_count"] = error_count
        return new_state
    except Exception as e:
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
    corrective_action = await analyze_tool_error.ainvoke(
        {"error_message": state["error_message"], "state": dict(state)}
    )

    error_summary = (
        f"Attempt {state['error_count']}: An error occurred. "
        f"Analysis: {corrective_action}. Retrying..."
    )

    return {
        "messages": [AIMessage(content=error_summary)],
        "is_error": False,  # Reset the error flag for the next attempt
    }


# --- Conditional Edges ---

def should_continue(state: GraphState) -> Literal["tools", "end"]:
    """Determines whether to call tools or end the process."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "end"


def after_tools(state: GraphState) -> Literal["agent", "error", "end"]:
    """
    Checks for tool execution errors and decides the next step.
    """
    if state.get("is_error"):
        if state.get("error_count", 0) >= settings.agent.max_retries:
            return "end"
        return "error"
    return "agent"


# --- Graph Definition ---
workflow = StateGraph(GraphState)

workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)   # still named "tools" in the graph
workflow.add_node("error", error_node)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", "end": END},
)

workflow.add_conditional_edges(
    "tools",
    after_tools,
    {"agent": "agent", "error": "error", "end": END},
)

# After an error is analyzed, loop back to the agent to retry.
workflow.add_edge("error", "agent")


# --- Graph Compilation Function ---
def create_agent_graph():
    """
    Factory function to create and compile the agent graph with its checkpointer.
    """
    checkpointer = RedisSaver.from_url(settings.REDIS_URL)
    # NEW: ensure index/setup is created once
    checkpointer.setup()
    agent_graph = workflow.compile(checkpointer=checkpointer)
    return agent_graph
