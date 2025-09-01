"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolExecutor

from app.agent.state import GraphState, Synopsis
from app.agent.tools import get_tools
from app.agent.tools.analysis_tools import analyze_tool_error
from app.agent.tools.planning_tools import InitialPlan
from app.core.config import settings
from app.services.llm_service import llm_service

# --- Agent Setup ---
tools = get_tools()
tool_executor = ToolExecutor(tools)

# --- Graph Nodes ---

async def agent_node(state: GraphState) -> dict:
    """
    The primary "thinking" node of the agent. It decides which tool to call next.
    """
    # Use the correct 'session_id' key to access the state.
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
        tools=[t.to_dict() for t in tools],
        session_id=str(session_id),
        trace_id=trace_id,
    )
    return {"messages": [response]}


async def tool_node(state: GraphState) -> dict:
    """Executes tools and returns the results."""
    last_message = state["messages"][-1]
    tool_messages = []
    is_error = False
    error_message = None
    error_count = state.get("error_count", 0)

    if hasattr(last_message, "tool_calls"):
        for tool_call in last_message.tool_calls:
            try:
                output = await tool_executor.ainvoke(tool_call)
                tool_messages.append(
                    ToolMessage(content=str(output), tool_call_id=tool_call["id"])
                )
            except Exception as e:
                # Capture exceptions and prepare for the error node
                error_message = f"Error executing tool {tool_call['name']}: {e}"
                tool_messages.append(
                    ToolMessage(
                        content=error_message,
                        tool_call_id=tool_call["id"],
                    )
                )
                is_error = True
                error_count += 1

    return {
        "messages": tool_messages,
        "is_error": is_error,
        "error_message": error_message,
        "error_count": error_count,
    }


async def error_node(state: GraphState) -> dict:
    """
    Handles errors by analyzing them and preparing for a retry.
    """
    # Use a tool to analyze the error and suggest a fix.
    # The state is passed as a dictionary to the tool for analysis.
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
    if isinstance(state["messages"][-1], AIMessage) and state["messages"][-1].tool_calls:
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
workflow.add_node("tools", tool_node)
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
    agent_graph = workflow.compile(checkpointer=checkpointer)
    return agent_graph