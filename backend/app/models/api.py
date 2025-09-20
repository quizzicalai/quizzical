from __future__ import annotations

"""
API Models (Pydantic Schemas)

This module defines the Pydantic models used for API request and response
validation. It intentionally avoids importing from the agent layer to prevent
circular imports. Agent-side code is free to import types from here.
"""

import enum
from typing import Any, Dict, List, Literal, Optional, Union, Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


# -----------------------------------------------------------------------------
# Base model with camelCase JSON
# -----------------------------------------------------------------------------
class APIBaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


# -----------------------------------------------------------------------------
# Core content models (authoritative and importable by agent layer)
# -----------------------------------------------------------------------------
class Synopsis(APIBaseModel):
    # Add discriminator so it can participate in a discriminated union
    type: Literal["synopsis"] = "synopsis"
    title: str
    summary: str


class CharacterProfile(APIBaseModel):
    name: str
    short_description: str
    profile_text: str
    image_url: Optional[str] = None


class AnswerOption(APIBaseModel):
    text: str
    image_url: Optional[str] = None


class Question(APIBaseModel):
    text: str
    image_url: Optional[str] = None
    options: List[AnswerOption]


# Some internal components still work with a "QuizQuestion" shape that uses
# `question_text` and a minimal option structure. Keep it to preserve API/editor
# compatibility where needed.
class QuizQuestion(APIBaseModel):
    # Add discriminator so it can participate in a discriminated union
    type: Literal["question"] = "question"
    question_text: str
    # typically [{"text": "...", "image_url": "..."}] but image key is optional
    options: List[Dict[str, str]]


class FinalResult(APIBaseModel):
    """Authoritative final result schema (imported by tools and agent)."""
    title: str
    image_url: Optional[str] = None
    description: str


# -----------------------------------------------------------------------------
# Requests / Responses for quiz flows
# -----------------------------------------------------------------------------
class StartQuizRequest(APIBaseModel):
    category: str = Field(..., min_length=3, max_length=100)
    cf_turnstile_response: str = Field(..., alias="cf-turnstile-response")


# For initial payload we allow either a synopsis or a "question-like" object.
# Make this a discriminated union on `type`.
DataUnion = Annotated[
    Union[Synopsis, QuizQuestion],
    Field(discriminator="type"),
]


class StartQuizPayload(APIBaseModel):
    # Keep this for external clarity; internal routing uses data.type
    type: Literal["synopsis", "question"]
    data: DataUnion


class CharactersPayload(APIBaseModel):
    type: Literal["characters"] = "characters"
    data: List[CharacterProfile]


class FrontendStartQuizResponse(APIBaseModel):
    quiz_id: UUID
    initial_payload: Optional[StartQuizPayload] = None
    characters_payload: Optional[CharactersPayload] = None


class NextQuestionRequest(APIBaseModel):
    quiz_id: UUID
    answer: Optional[str] = None


class ProceedRequest(APIBaseModel):
    quiz_id: UUID


# -----------------------------------------------------------------------------
# Status polling
# -----------------------------------------------------------------------------
class ProcessingResponse(APIBaseModel):
    status: Literal["processing"]
    quiz_id: UUID


class QuizStatusQuestion(APIBaseModel):
    status: Literal["active"]
    type: Literal["question"]
    data: Question


class QuizStatusResult(APIBaseModel):
    status: Literal["finished"]
    type: Literal["result"]
    data: FinalResult


QuizStatusResponse = Union[QuizStatusQuestion, QuizStatusResult, ProcessingResponse]


# -----------------------------------------------------------------------------
# Public result & feedback
# -----------------------------------------------------------------------------
class ShareableResultResponse(APIBaseModel):
    title: str
    description: str
    image_url: Optional[str] = None
    category: Optional[str] = None
    created_at: Optional[str] = None


class FeedbackRatingEnum(str, enum.Enum):
    UP = "up"
    DOWN = "down"


class FeedbackRequest(APIBaseModel):
    quiz_id: UUID
    rating: FeedbackRatingEnum
    text: Optional[str] = None


# -----------------------------------------------------------------------------
# Optional: serialized graph state (kept loose to avoid importing agent state)
# -----------------------------------------------------------------------------
class PydanticGraphState(APIBaseModel):
    """
    A serialization-friendly view of the agent state for persistence (e.g., Redis).
    Types are intentionally generic to avoid import cycles.
    """
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    session_id: UUID
    trace_id: str
    category: str

    error_count: int = 0

    rag_context: Optional[List[Dict[str, Any]]] = None
    # Keep the typing as Synopsis to preserve editor help; at runtime the agent
    # may still place a plain dict here — the endpoint normalizes it.
    category_synopsis: Optional[Synopsis] = None
    ideal_archetypes: List[str] = Field(default_factory=list)
    generated_characters: List[CharacterProfile] = Field(default_factory=list)
    generated_questions: List[QuizQuestion] = Field(default_factory=list)
    final_result: Optional[FinalResult] = None
