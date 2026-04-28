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
from typing import Annotated, Any

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
    messages: Annotated[list[BaseMessage], add_messages]

    # Session identifiers & user input
    session_id: uuid.UUID
    trace_id: str
    category: str

    # Agent control flow
    agent_plan: dict[str, Any] | None
    error_count: int
    error_message: str | None
    is_error: bool

    # Router gate for question generation (set by /quiz/proceed)
    ready_for_questions: bool

    # --- SYNOPSIS ---
    synopsis: Synopsis | None
    outcome_kind: str | None
    creativity_mode: str | None
    topic_analysis: dict[str, Any] | None  # raw analysis dict

    # Optional retrieval-augmented context (kept symmetrical with the transport
    # ``AgentGraphStateModel``; consumed by retrieval-aware tools when present).
    rag_context: list[dict[str, Any]] | None

    # Planned + generated artifacts
    ideal_archetypes: list[str] | None
    generated_characters: list[CharacterProfile] | None
    generated_questions: list[QuizQuestion] | None

    # Adaptive flow
    quiz_history: list[QuestionAnswer] | None
    baseline_count: int | None                 # number of baseline questions generated
    baseline_ready: bool | None                # explicit baseline flag for router
    should_finalize: bool | None               # set by decider node
    current_confidence: float | None           # set when finishing

    # Final assembly result (if/when persisted or exposed)
    final_result: FinalResult | None

    # Observability (non-authoritative)
    last_served_index: int | None


__all__ = [
    "GraphState",
    # re-export canonical content models for convenience
    "Synopsis",
    "CharacterProfile",
    "QuizQuestion",
    "FinalResult",
]
