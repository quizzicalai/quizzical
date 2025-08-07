"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolExecutor

from app.agent.state import GraphState
from app.agent.tools import get_tools
from app.core.config import settings
from app.services.llm_service import get_llm

# --- Agent Setup ---
tools = get_tools()
tool_executor = ToolExecutor(tools)
llm = get_llm()
llm_with_tools = llm.bind_tools(tools)


# --- Graph Nodes ---
def agent_node(state: GraphState) -> dict:
    """Invokes the LLM to get the next action or final response."""
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


def tool_node(state: GraphState) -> dict:
    """Executes tools and returns the results."""
    last_message = state["messages"][-1]
    tool_messages = []
    for tool_call in last_message.tool_calls:
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

# Add nodes for the core agent loop
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)

# The entry point is the agent itself
workflow.set_entry_point("agent")

# The main conditional edge decides if we need to run tools or if we're done
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        "end": END,
    },
)

# After running tools, we always return to the agent to process the results
workflow.add_edge("tools", "agent")

# Add the Redis checkpointer for session persistence
checkpointer = RedisSaver.from_conn_string(settings.REDIS_URL)

# Compile the final graph
agent_graph = workflow.compile(checkpointer=checkpointer)