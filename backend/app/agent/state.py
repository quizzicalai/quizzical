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

SURGICAL CHANGES (to enable baseline questions):
- Add `ready_for_questions: bool` (router gate after characters).
- Add `synopsis: Optional[Synopsis]` alongside `category_synopsis` to avoid
  synopsis loss during hydration/validation and tolerate both keys.
- Keep lists optional where partial state is expected during graph execution.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Canonical content models (live in app.agent.schemas)
from app.agent.schemas import (
    Synopsis,
    CharacterProfile,
    QuizQuestion,
    QuestionAnswer,
)

# Re-use canonical API models where appropriate
from app.models.api import FinalResult


# --- Main Agent State Definition ---

class GraphState(TypedDict, total=False):
    """
    Represents the state of our agent's workflow.
    This mirrors the shape used across the app and API layers.

    `total=False` allows partial/iterative state updates during graph execution.
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

    # Router gate for question generation (set by /quiz/proceed)
    ready_for_questions: bool

    # Retrieved & Generated Content
    rag_context: Optional[List[Dict[str, Any]]]

    # --- SYNOPSIS (back-compat) ---
    # Some nodes/services refer to `synopsis`, while others use `category_synopsis`.
    # Keep both optional entries to avoid dropping data during hydration/validation.
    synopsis: Optional[Synopsis]
    category_synopsis: Optional[Synopsis]

    # Planned + generated artifacts
    ideal_archetypes: Optional[List[str]]
    generated_characters: Optional[List[CharacterProfile]]
    generated_questions: Optional[List[QuizQuestion]]

    # Adaptive flow
    quiz_history: Optional[List[QuestionAnswer]]  # typed Q&A
    baseline_count: Optional[int]                 # number of baseline questions generated
    should_finalize: Optional[bool]               # set by decider node
    current_confidence: Optional[float]           # set when finishing

    # Final assembly result (if/when persisted or exposed)
    final_result: Optional[FinalResult]


__all__ = [
    "GraphState",
    # re-export canonical content models for convenience
    "Synopsis",
    "CharacterProfile",
    "QuizQuestion",
    "FinalResult",
]
