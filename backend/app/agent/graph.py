# backend/app/agent/graph.py
"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph_redis import RedisSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolExecutor

from app.agent.state import GraphState
from app.agent.tools import get_tools
from app.agent.tools.planning_tools import InitialPlan
from app.core.config import settings
from app.services.llm_service import llm_service

# --- Agent Setup ---
tools = get_tools()
tool_executor = ToolExecutor(tools)

# --- Graph Nodes ---
async def agent_node(state: GraphState) -> dict:
    """
    The primary "thinking" node of the agent.

    On the first turn, it creates the initial plan. On subsequent turns,
    it decides which tool to call next.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    messages = state["messages"]

    # On the first turn, the agent's job is to create a plan.
    if len(messages) == 1:
        category = messages[0].content
        initial_plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=session_id,
            trace_id=trace_id,
        )
        # Create a human-readable confirmation message to add to the transcript
        plan_summary = (
            f"Plan created for '{category}'. Synopsis: '{initial_plan.synopsis}'. "
            f"Will now find or create characters for the following archetypes: {initial_plan.ideal_archetypes}"
        )
        return {
            "messages": [AIMessage(content=plan_summary)],
            "category_synopsis": initial_plan.synopsis,
            "ideal_archetypes": initial_plan.ideal_archetypes,
        }

    # For all subsequent turns, decide the next tool to call.
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=messages,
        tools=[t.to_dict() for t in tools],
        session_id=session_id,
        trace_id=trace_id,
    )
    return {"messages": [response]}


def tool_node(state: GraphState) -> dict:
    """Executes tools and returns the results."""
    last_message = state["messages"][-1]
    tool_messages = []
    # The list of tool calls is on the AIMessage
    if hasattr(last_message, "tool_calls"):
        for tool_call in last_message.tool_calls:
            # The tool executor will invoke the correct tool with the correct arguments
            output = tool_executor.invoke(tool_call)
            tool_messages.append(
                ToolMessage(content=str(output), tool_call_id=tool_call["id"])
            )
    return {"messages": tool_messages}


# --- Conditional Edges ---
def should_continue(state: GraphState) -> Literal["tools", "end"]:
    """Determines whether to call tools or end the process."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "end"


# --- Graph Definition & Compilation ---
workflow = StateGraph(GraphState)

workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", "end": END},
)

workflow.add_edge("tools", "agent")

checkpointer = RedisSaver.from_url(settings.REDIS_URL)
agent_graph = workflow.compile(checkpointer=checkpointer)