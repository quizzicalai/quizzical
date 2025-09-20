# backend/app/agent/state.py
"""
Agent State

Defines the TypedDict used by the LangGraph and re-exports the content
models the agent works with. The actual Pydantic model definitions live
in `app.agent.schemas` so that:
  - `llm_service` can build OpenAI JSON Schemas from a single source, and
  - we avoid circular imports between agent modules and tools.

NOTE:
- The LangGraph in `agent/graph.py` defines its own `GraphState` for wiring.
  This file keeps a compatible state type that other parts of the system
  may import (e.g., repos, API models). It’s fine for both to coexist.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Canonical content models (moved here from the old local definitions)
from app.agent.schemas import (
    Synopsis,
    CharacterProfile,
    QuizQuestion,
)

# Re-use canonical API models where appropriate
from app.models.api import FinalResult


# --- Main Agent State Definition ---

class GraphState(TypedDict):
    """
    Represents the state of our agent's workflow.
    This mirrors the shape used across the app and API layers.
    """
    # Conversation history (append-only)
    messages: Annotated[List[BaseMessage], add_messages]

    # Session identifiers & user input
    session_id: uuid.UUID
    trace_id: str
    category: str

    # Agent control flow
    error_count: int
    error_message: Optional[str]
    is_error: bool

    # Retrieved & Generated Content
    rag_context: Optional[List[Dict[str, Any]]]
    category_synopsis: Optional[Synopsis]
    ideal_archetypes: List[str]
    generated_characters: List[CharacterProfile]
    generated_questions: List[QuizQuestion]

    # Final assembly result (if/when persisted or exposed)
    final_result: Optional[FinalResult]
