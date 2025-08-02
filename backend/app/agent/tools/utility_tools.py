"""
Agent Tools: Utility & Persistence

This module contains utility tools for final actions, such as saving
the completed session to the database.
"""
from typing import Dict

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# --- Pydantic Models for Structured Inputs ---

class PersistSessionInput(BaseModel):
    """Input for the persist_session_to_database tool."""
    final_agent_state: Dict = Field(..., description="The complete, final state of the agent graph.")


# --- Tool Definitions ---

@tool
async def persist_session_to_database(input_data: PersistSessionInput) -> str:
    """
    Saves the complete, successful quiz session to the long-term database.
    This should be one of the final actions in a successful workflow.
    """
    # This tool would use the SessionRepository to save the data.
    # For now, it's a placeholder.
    print("Persisting final session state to the database...")
    return "Session successfully saved."
