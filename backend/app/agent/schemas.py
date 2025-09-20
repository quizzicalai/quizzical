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
- Keep fields strictly typed (no `Any` in lists) so the compiled JSON
  Schema is valid for OpenAI `response_format=json_schema`.
- The "state" models (e.g. `QuizQuestion`) intentionally use
  `List[Dict[str, str]]` to match what the rest of the app expects today,
  while the *structured output* variants (e.g. `QuestionOption`,
  `QuestionOut`, `QuestionList`) are stricter and ideal for LLM outputs.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from pydantic import BaseModel, Field

# If tools define their own typed outputs (e.g., InitialPlan), import them here.
# This import is deliberately one-way (tools do NOT import schemas) to avoid cycles.
try:
    from app.agent.tools.planning_tools import InitialPlan  # noqa: F401
except Exception:  # pragma: no cover
    InitialPlan = None  # type: ignore


# ---------------------------------------------------------------------------
# Core content models (re-used across agent & API)
# ---------------------------------------------------------------------------

class Synopsis(BaseModel):
    """High-level summary of the quiz category."""
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)


class CharacterProfile(BaseModel):
    """One playable/guessable character."""
    name: str = Field(..., min_length=1)
    short_description: str = Field(..., min_length=1)
    profile_text: str = Field(..., min_length=1)
    image_url: Optional[str] = None


class QuizQuestion(BaseModel):
    """
    Question shape used by the agent state and API responses today.
    Kept as `List[Dict[str, str]]` for maximum compatibility with
    existing code paths (renderers, normalizers, etc).
    """
    question_text: str = Field(..., min_length=1)
    # e.g., [{"text": "Option A", "image_url": "..."}, {"text": "Option B"}]
    options: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Strict structured-output variants (preferred for LLM responses)
# ---------------------------------------------------------------------------

class QuestionOption(BaseModel):
    text: str = Field(..., min_length=1)
    image_url: Optional[str] = None


class QuestionOut(BaseModel):
    question_text: str = Field(..., min_length=1)
    options: List[QuestionOption]


class QuestionList(BaseModel):
    questions: List[QuestionOut]


class CharacterArchetypeList(BaseModel):
    """
    For tools that return *names* of archetypes (not full profiles).
    """
    archetypes: List[str]


class CharacterSelection(BaseModel):
    """
    For tools that must pick winners from a candidate list.
    """
    selected_names: List[str]


class SafetyCheck(BaseModel):
    """
    Minimal safety gate result that works for structured output.
    """
    allowed: bool
    categories: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    rationale: Optional[str] = None


class ErrorAnalysis(BaseModel):
    """
    Analysis used by recovery/diagnostics tools.
    """
    retryable: bool
    reason: str
    details: Optional[Dict[str, str]] = None


class FailureExplanation(BaseModel):
    """
    Human-friendly explanation to surface to a user or log.
    """
    message: str
    tips: Optional[List[str]] = None


class ImagePrompt(BaseModel):
    """
    Enhanced prompt pack for downstream image generation.
    """
    prompt: str
    negative_prompt: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry: map tool_name → expected response model
# (Used by llm_service if the caller does not pass `response_model`.)
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    # Planning / bootstrapping
    "initial_planner": InitialPlan,            # from app.agent.tools.planning_tools
    "synopsis_generator": Synopsis,
    "character_list_generator": CharacterArchetypeList,

    # Character pipelines
    "profile_writer": CharacterProfile,
    "profile_improver": CharacterProfile,
    "final_profile_writer": CharacterProfile,
    "character_selector": CharacterSelection,

    # Question generation
    "question_generator": QuestionList,        # strict structure for LLM outputs
    "next_question_generator": QuestionOut,

    # Misc / safety / diagnostics
    "safety_checker": SafetyCheck,
    "error_analyzer": ErrorAnalysis,
    "failure_explainer": FailureExplanation,
    "image_prompt_enhancer": ImagePrompt,
}


def schema_for(tool_name: str) -> Optional[Type[BaseModel]]:
    """
    Convenience accessor for callers that want to look up the
    default response model for a configured tool.
    """
    model = SCHEMA_REGISTRY.get(tool_name)
    # If a tool isn't registered (or InitialPlan is unavailable in local env),
    # return None so the caller can still pass an explicit model.
    return model
