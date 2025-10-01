# backend/app/agent/schemas.py
"""
Shared agent ↔ LLM schemas (Pydantic)

Purpose
-------
- Single source of truth for the shapes we **ask** the LLM to produce
  (used by `llm_service.get_structured_response` to build OpenAI
  `response_format={"type":"json_schema", ...}` and to validate responses).
- Re-export lightweight models used by the agent state so graph code
  does not depend on tool modules and vice-versa.

Notes
-----
- Fields are strictly typed and extra fields are forbidden to align with
  OpenAI's strict json_schema output (additionalProperties: false).
- The "state" models (e.g. `QuizQuestion`) intentionally use
  `List[Dict[str, str]]` to match current app expectations, while
  structured-output variants (e.g. `QuestionOption`, `QuestionOut`) are
  stricter and ideal for LLM outputs.

Nice-to-have
------------
- We also export a ready-to-use strict JSON Schema envelope for the
  InitialPlan tool output:
    InitialPlan = {
        "name": "...",
        "schema": {... "additionalProperties": False, ...},
        "strict": True,
    }
  This can be passed directly as `response_format` to OpenAI/Azure if desired.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type, Optional as Opt, Literal, Any

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel  # no runtime use; reserved for future
from pydantic import AliasChoices  # tolerant field aliasing for LLM variants
from uuid import UUID


class StrictBase(BaseModel):
    """All models forbid extra fields to keep JSON Schema strict."""
    # Keep JSON Schema strict and allow population by alias for tolerant inputs
    model_config = {
        "extra": "forbid",
        "populate_by_name": True,          # allow using field names as well as aliases
        "str_strip_whitespace": True,      # common normalization for LLM text
    }


# If tools define their own typed outputs (e.g., InitialPlan), import them here.
# Import under an alias to avoid naming collision with the JSON Schema dict below.
try:
    from app.agent.tools.planning_tools import InitialPlan as _InitialPlanModel  # noqa: F401
except Exception:  # pragma: no cover
    _InitialPlanModel = None  # type: ignore


# ---------------------------------------------------------------------------
# Core content models (re-used across agent & API)
# ---------------------------------------------------------------------------

class Synopsis(StrictBase):
    """High-level summary of the quiz category.

    Changes:
    - `summary` now allows empty string (min_length=0) to avoid validation
      failures when upstream creates a placeholder/empty summary.
    - Accept tolerant input aliases commonly produced by LLMs:
        - synopsis_text, synopsis, summary
    """
    title: str = Field(..., min_length=1)
    summary: str = Field(
        default="",
        min_length=0,
        validation_alias=AliasChoices("summary", "synopsis_text", "synopsis"),
    )


class CharacterProfile(StrictBase):
    """One playable/guessable character.

    Changes:
    - `short_description` and `profile_text` allow empty strings (min_length=0)
      so fallbacks in tools/graph don't fail validation.
    """
    name: str = Field(..., min_length=1)
    short_description: str = Field(default="", min_length=0)
    profile_text: str = Field(default="", min_length=0)
    image_url: Opt[str] = None  # default keeps it OPTIONAL in the JSON Schema



class QuizQuestion(StrictBase):
    """
    Question shape used by the agent state and API responses today.
    Kept as `List[Dict[str, str]]` for maximum compatibility with
    existing code paths (renderers, normalizers, etc).
    """
    question_text: str = Field(..., min_length=1)
    options: List[Dict[str, str]]  # e.g., [{"text": "A", "image_url": "..."}]


# ---------------------------------------------------------------------------
# Strict structured-output variants (preferred for LLM responses)
# ---------------------------------------------------------------------------

class QuestionOption(StrictBase):
    text: str = Field(..., min_length=1)
    image_url: Opt[str] = None


class QuestionOut(StrictBase):
    question_text: str = Field(..., min_length=1)
    options: List[QuestionOption]


class QuestionList(StrictBase):
    questions: List[QuestionOut]


class CharacterArchetypeList(StrictBase):
    """For tools that return *names* of archetypes (not full profiles)."""
    archetypes: List[str]


class CharacterSelection(StrictBase):
    """For tools that must pick winners from a candidate list."""
    selected_names: List[str]


class SafetyCheck(StrictBase):
    """Minimal safety gate result that works for structured output."""
    allowed: bool
    categories: Opt[List[str]] = None
    warnings: Opt[List[str]] = None
    rationale: Opt[str] = None


class ErrorAnalysis(StrictBase):
    """Analysis used by recovery/diagnostics tools."""
    retryable: bool
    reason: str
    details: Opt[Dict[str, str]] = None


class FailureExplanation(StrictBase):
    """Human-friendly explanation to surface to a user or log."""
    message: str
    tips: Opt[List[str]] = None


class ImagePrompt(StrictBase):
    """Enhanced prompt pack for downstream image generation."""
    prompt: str
    negative_prompt: Opt[str] = None


# ---------------------------------------------------------------------------
# NEW: typed history entry & decider output (for adaptive flow)
# ---------------------------------------------------------------------------

class QuestionAnswer(StrictBase):
    question_index: int = Field(default=0, ge=0)
    question_text: str = Field(..., min_length=1)
    answer_text: str = Field(..., min_length=1)
    option_index: Opt[int] = None


class NextStepDecision(StrictBase):
    action: Literal["ASK_ONE_MORE_QUESTION", "FINISH_NOW"]
    winning_character_name: Opt[str] = None
    confidence: Opt[float] = None  # 0.0–1.0 (LLM may return 0–100; caller normalizes)


class AgentGraphStateModel(StrictBase):
    """
    Canonical cache/transport model for agent state. This mirrors the GraphState keys
    used throughout the app, while remaining strict for storage and validation.
    """
    session_id: UUID
    trace_id: str

    category: str
    # Redis holds dict-ified messages; use safe default factory
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    is_error: bool = False
    error_message: Opt[str] = None
    error_count: int = 0

    # Optional content retrieved/built during execution (present in GraphState)
    rag_context: Opt[List[Dict[str, Any]]] = None
    outcome_kind: Opt[str] = None
    creativity_mode: Opt[str] = None

    # Single canonical synopsis key
    category_synopsis: Opt[Synopsis] = None

    # Planned + generated artifacts
    ideal_archetypes: List[str] = Field(default_factory=list)
    generated_characters: List[CharacterProfile] = Field(default_factory=list)
    generated_questions: List[QuizQuestion] = Field(default_factory=list)

    # Adaptive flow
    quiz_history: List[Dict[str, Any]] = Field(default_factory=list)  # keep as dicts for flexibility in v0
    baseline_count: int = 0
    baseline_ready: bool = False
    ready_for_questions: bool = False
    should_finalize: Opt[bool] = None
    current_confidence: Opt[float] = None

    # Final assembly result (if/when persisted or exposed)
    final_result: Opt[Dict[str, Any]] = None
    last_served_index: Opt[int] = None


# ---------------------------------------------------------------------------
# Optional: strict JSON Schema object for InitialPlan (clean-at-source)
# ---------------------------------------------------------------------------

# This object matches OpenAI/Azure `response_format={"type":"json_schema", ...}`
# usage patterns, with `additionalProperties: False` and `strict: True`.
InitialPlan: Dict = {
    "name": "InitialPlan",
    "schema": {
        "title": "InitialPlan",
        "description": "Output of the initial planning stage.",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "synopsis": {
                "type": "string",
                "description": "Engaging synopsis (2–3 sentences) for the quiz category.",
            },
            "ideal_archetypes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "4–6 ideal character archetypes.",
            },
        },
        "required": ["synopsis", "ideal_archetypes"],
    },
    "strict": True,
}


# ---------------------------------------------------------------------------
# Registry: map tool_name → expected response model
# (Used by llm_service if the caller does not pass `response_model`.)
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    # Planning / bootstrapping
    # Keep the registry pointing to the Pydantic model type for compatibility
    # with llm_service.get_structured_response(...).
    "initial_planner": _InitialPlanModel,      # from app.agent.tools.planning_tools
    "synopsis_generator": Synopsis,
    "character_list_generator": CharacterArchetypeList,

    # Character pipelines
    "profile_writer": CharacterProfile,
    "profile_improver": CharacterProfile,
    "final_profile_writer": CharacterProfile,
    "character_selector": CharacterSelection,

    # Question generation
    "question_generator": QuestionList,
    "next_question_generator": QuestionOut,

    # Misc / safety / diagnostics
    "safety_checker": SafetyCheck,
    "error_analyzer": ErrorAnalysis,
    "failure_explainer": FailureExplanation,
    "image_prompt_enhancer": ImagePrompt,

    # Adaptive flow controller
    "decision_maker": NextStepDecision,
}


def schema_for(tool_name: str) -> Optional[Type[BaseModel]]:
    """
    Convenience accessor for callers that want to look up the
    default response model for a configured tool.

    Note: For InitialPlan, this returns the Pydantic class used by the
    existing tools. If you want the strict JSON Schema dict instead,
    import `InitialPlan` (the dict) from this module directly and pass
    it as `response_format` to OpenAI/Azure.
    """
    return SCHEMA_REGISTRY.get(tool_name)
