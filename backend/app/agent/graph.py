"""
Main Agent Graph

This module defines the core LangGraph agent that orchestrates the entire
quiz generation SAGA.

The graph is designed to be highly flexible and resilient, using a dynamic
tool-calling loop and built-in error handling for self-correction.
"""

from langchain_core.messages import ToolMessage
from langchain_core.utils.function_calling import format_tool_to_openai_tool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolExecutor

from app.agent.state import GraphState
from app.agent.tools import tool_registry
from app.services.llm_service import llm_service

# --- Agent Setup ---

# 1. Create the Tool Executor
tool_executor = ToolExecutor(tool_registry)

# 2. Convert our tools into a schema that the LLM can understand.
tool_schemas = [format_tool_to_openai_tool(t) for t in tool_registry]


# --- Graph Nodes ---

async def agent_node(state: GraphState):
    """
    The primary node that calls the LLM planner to decide the next action.
    This is the "brain" of the agent.
    """
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=state["messages"],
        tools=tool_schemas,
        trace_id=state["trace_id"],
        session_id=str(state["session_id"]),
    )
    # The agent's response is the new message added to the state.
    return {"messages": [response]}


async def tool_node(state: GraphState):
    """
    This node executes the tool chosen by the agent and handles errors.
    """
    last_message = state["messages"][-1]
    error_count = state.get("error_count", 0)
    
    try:
        tool_response = await tool_executor.ainvoke(last_message)
        return {"messages": [tool_response]}
    except Exception as e:
        # If the tool fails, create a ToolMessage with the error and
        # increment the error counter in the state.
        error_count += 1
        tool_response = ToolMessage(
            content=f"Error executing tool '{last_message.tool_calls[0]['name']}': {e}",
            tool_call_id=last_message.tool_calls[0]["id"],
        )
        return {"messages": [tool_response], "error_count": error_count}


# --- Conditional Edges ---

def should_continue(state: GraphState) -> str:
    """Determines whether to continue the agent loop or end the process."""
    if state["messages"][-1].tool_calls:
        return "continue"
    return "end"


def after_tool_execution(state: GraphState) -> str:
    """
    Checks for errors after a tool has been executed and decides whether
    to trigger the self-correction loop.
    """
    last_message = state["messages"][-1]
    if isinstance(last_message, ToolMessage) and "Error executing tool" in last_message.content:
        if state.get("error_count", 0) >= 3:  # Max retries
            return "end_with_error"
        # Route back to the agent to analyze the error and self-correct.
        return "self_correct"
    
    # If the tool executed successfully, continue the main loop.
    return "continue"


# --- Graph Definition ---

def create_agent_graph() -> StateGraph:
    """Builds and compiles the main LangGraph for the quiz generation agent."""
    workflow = StateGraph(GraphState)

    workflow.add_node("agent", agent_node)
    workflow.add_node("tool_executor", tool_node)
    workflow.add_node("error_handler", lambda state: {"messages": ["Ending due to persistent errors."]})

    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"continue": "tool_executor", "end": END},
    )

    workflow.add_conditional_edges(
        "tool_executor",
        after_tool_execution,
        {
            "continue": "agent",
            "self_correct": "agent",
            "end_with_error": "error_handler",
        },
    )
    
    workflow.add_edge("error_handler", END)

    app = workflow.compile()
    return app

agent_graph = create_agent_graph()
