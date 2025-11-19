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
  synopsis loss during hydration/validation and tolerate both keys.
- Keep lists optional where partial state is expected during graph execution.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# Canonical content models (live in app.agent.schemas)
from app.agent.schemas import (
    CharacterProfile,
    QuestionAnswer,
    QuizQuestion,
    Synopsis,
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
    agent_plan: Optional[Dict[str, Any]]
    error_count: int
    error_message: Optional[str]
    is_error: bool

    # Router gate for question generation (set by /quiz/proceed)
    ready_for_questions: bool

    # --- SYNOPSIS ---
    synopsis: Optional[Synopsis]
    outcome_kind: Optional[str]
    creativity_mode: Optional[str]
    topic_analysis: Optional[Dict[str, Any]]  # raw analysis dict

    # Planned + generated artifacts
    ideal_archetypes: Optional[List[str]]
    generated_characters: Optional[List[CharacterProfile]]
    generated_questions: Optional[List[QuizQuestion]]

    # Adaptive flow
    quiz_history: Optional[List[QuestionAnswer]]
    baseline_count: Optional[int]                 # number of baseline questions generated
    baseline_ready: Optional[bool]                # explicit baseline flag for router
    should_finalize: Optional[bool]               # set by decider node
    current_confidence: Optional[float]           # set when finishing

    # Final assembly result (if/when persisted or exposed)
    final_result: Optional[FinalResult]

    # Observability (non-authoritative)
    last_served_index: Optional[int]


__all__ = [
    "GraphState",
    # re-export canonical content models for convenience
    "Synopsis",
    "CharacterProfile",
    "QuizQuestion",
    "FinalResult",
]
