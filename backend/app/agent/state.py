"""
Agent State

This module defines the TypedDict for the agent's state. This state object
acts as the "short-term memory" for a single quiz generation SAGA. It is passed
between each node in the LangGraph, accumulating data and guiding the agent's
decisions.

Using a TypedDict with Annotated fields is the modern best practice for LangGraph
as it makes state updates explicit and robust.
"""
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# --- Data Models for Generated Content ---
# Using Pydantic models within the state provides strong type-safety for the
# content the agent creates and manipulates.

class CharacterProfile(BaseModel):
    """A structured representation of a generated character."""
    name: str
    short_description: str
    profile_text: str
    image_url: Optional[str] = None

class QuizQuestion(BaseModel):
    """A structured representation of a single quiz question."""
    question_text: str
    options: List[Dict[str, str]] # e.g., [{"text": "...", "image_url": "..."}]

class FinalResult(BaseModel):
    """The final, personalized result for the user."""
    title: str
    description: str
    image_url: str


# --- Main Agent State Definition ---

class GraphState(TypedDict):
    """
    Represents the state of our agent's workflow.

    Attributes:
        messages: The sequence of messages defining the conversation history.
                  This is an accumulating field.
        session_id: The unique identifier for this quiz session.
        trace_id: The unique trace ID for observability.
        category: The raw category provided by the user.
        error_count: A counter for tracking retries and self-correction attempts.
        rag_context: The historical session data retrieved for context.
        category_synopsis: The rich, semantic synopsis of the category.
        generated_characters: A list of finalized character profiles for the quiz.
        generated_questions: A list of the questions that have been generated.
        final_result: The final, personalized result for the user.
    """

    # --- Agent's Core Memory ---
    # `add_messages` is a special operator from LangGraph that ensures new
    # messages are always appended to the list, not overwritten.
    messages: Annotated[List[BaseMessage], add_messages]

    # --- Session Identifiers & User Input ---
    session_id: uuid.UUID
    trace_id: str
    category: str

    # --- Agent Control Flow ---
    error_count: int

    # --- Retrieved & Generated Content ---
    rag_context: Optional[List[Dict[str, Any]]]
    category_synopsis: Optional[str]
    generated_characters: List[CharacterProfile]
    generated_questions: List[QuizQuestion]
    final_result: Optional[FinalResult]
