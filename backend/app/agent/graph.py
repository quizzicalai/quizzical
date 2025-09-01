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

    if hasattr(last_message, "tool_calls"):
        for tool_call in last_message.tool_calls:
            try:
                output = await tool_executor.ainvoke(tool_call)
                tool_messages.append(
                    ToolMessage(content=str(output), tool_call_id=tool_call["id"])
                )
            except Exception as e:
                # Capture exceptions during tool execution and format as an error message.
                error_message = f"Error executing tool {tool_call['name']}: {e}"
                tool_messages.append(
                    ToolMessage(
                        content=error_message,
                        tool_call_id=tool_call["id"],
                        # Custom flag to identify error messages
                        additional_kwargs={"is_error": True},
                    )
                )
    return {"messages": tool_messages}


async def error_node(state: GraphState) -> dict:
    """
    Handles errors by analyzing them and preparing for a retry.
    """
    last_tool_message = state["messages"][-1]
    error_message = last_tool_message.content
    
    # Increment the error counter.
    error_count = state.get("error_count", 0) + 1

    # Use a tool to analyze the error and suggest a fix.
    corrective_action = await analyze_tool_error.ainvoke(
        {"error_message": error_message, "state": state}
    )
    
    error_summary = (
        f"Attempt {error_count}: An error occurred. "
        f"Analysis: {corrective_action}. Retrying..."
    )
    
    return {
        "messages": [AIMessage(content=error_summary)],
        "error_count": error_count,
    }


# --- Conditional Edges ---

def should_continue(state: GraphState) -> Literal["tools", "end"]:
    """Determines whether to call tools or end the process."""
    if isinstance(state["messages"][-1], AIMessage) and state["messages"][-1].tool_calls:
        return "tools"
    return "end"


def after_tools(state: GraphState) -> Literal["agent", "error"]:
    """
    Checks for tool execution errors and decides the next step.
    If an error occurred, it routes to the error handler. Otherwise, it continues.
    """
    last_message = state["messages"][-1]
    if isinstance(last_message, ToolMessage) and last_message.additional_kwargs.get("is_error"):
        # If we have exceeded the max retries, end the process.
        if state.get("error_count", 0) >= settings.agent.max_retries:
            return END
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
    {"agent": "agent", "error": "error", END: END},
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
