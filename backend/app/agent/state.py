# backend/app/agent/state.py
"""
Agent State

Defines the TypedDict used by the LangGraph and the content models used by
the agent. To avoid circular imports, we import FinalResult (and optionally
re-use other API models) from app.models.api.
"""

import uuid
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

# Re-use canonical API models where appropriate
from app.models.api import FinalResult


# --- Data Models for Generated Content (agent-side) ---

class Synopsis(BaseModel):
    title: str
    summary: str


class CharacterProfile(BaseModel):
    name: str
    short_description: str
    profile_text: str
    image_url: Optional[str] = None


class QuizQuestion(BaseModel):
    question_text: str
    # e.g., [{"text": "Option A", "image_url": "..."}, {"text": "Option B"}]
    options: List[Dict[str, str]]


# --- Main Agent State Definition ---

class GraphState(TypedDict):
    """
    Represents the state of our agent's workflow.
    """
    # Conversation history
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
    final_result: Optional[FinalResult]
