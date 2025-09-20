# backend/app/models/api.py

from __future__ import annotations

"""
API Models (Pydantic Schemas)

This module defines the Pydantic models used for API request and response
validation. These models act as the "contract" for the API, ensuring that
all data flowing into and out of the application is structured, typed, and
validated.
"""

import enum
from typing import Any, Dict, List, Literal, Optional, Union, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# NOTE:
# Do NOT import from app.agent.state at runtime; that creates a circular import.
# We use forward references (strings) and optional TYPE_CHECKING-only imports.
if TYPE_CHECKING:
    from app.agent.state import CharacterProfile, QuizQuestion, Synopsis


class APIBaseModel(BaseModel):
    """
    A base model for all API schemas to configure camelCase conversion.
    This ensures the JSON API uses camelCase, while the Python code
    remains idiomatic with snake_case.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        arbitrary_types_allowed=True,  # Allows complex types like UUID
    )


# ---------------------------------------------------------------------------
# Enums for consistent value constraints
# ---------------------------------------------------------------------------
class FeedbackRatingEnum(str, enum.Enum):
    """Enum for user feedback ratings."""
    UP = "up"
    DOWN = "down"


# ---------------------------------------------------------------------------
# Models for Quiz Start and Progression
# ---------------------------------------------------------------------------
class StartQuizRequest(APIBaseModel):
    """Schema for the request body of the POST /api/quiz/start endpoint."""
    category: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="The user-provided category for the quiz.",
    )
    cf_turnstile_response: str = Field(
        ...,
        alias="cf-turnstile-response",
        description="The validation token from the Cloudflare Turnstile widget.",
    )


class StartQuizPayload(APIBaseModel):
    """
    A container for the initial data sent to the frontend that is not
    the characters list. It can be either a synopsis to show the user,
    or the first question directly.
    """
    type: Literal["synopsis", "question"]
    # Forward refs avoid importing from app.agent.state at runtime.
    data: Union["QuizQuestion", "Synopsis"]


class CharactersPayload(APIBaseModel):
    """
    A dedicated payload to transport the generated characters to the frontend.
    This avoids overloading StartQuizPayload with list-of-characters.
    """
    type: Literal["characters"] = "characters"
    data: List["CharacterProfile"]


class FrontendStartQuizResponse(APIBaseModel):
    """
    The response model for the /quiz/start endpoint that matches the
    frontend's expectations.
    """
    quiz_id: UUID
    # Optionally return a synopsis or an initial question
    initial_payload: Optional[StartQuizPayload] = None
    # Optionally return characters (once generated or if ready in time)
    characters_payload: Optional[CharactersPayload] = None


class AnswerOption(APIBaseModel):
    """Schema for a single multiple-choice answer option."""
    text: str
    image_url: Optional[str] = None


class Question(APIBaseModel):
    """Schema for a single quiz question and its options."""
    text: str
    image_url: Optional[str] = None
    options: List[AnswerOption]


class NextQuestionRequest(APIBaseModel):
    """
    Schema for the request body of the POST /api/quiz/next endpoint.
    The answer is optional to support transitional states or defensive handling.
    """
    quiz_id: UUID
    answer: Optional[str] = Field(
        default=None,
        description="The user's selected answer text (optional to allow defensive flows).",
    )


class ProceedRequest(APIBaseModel):
    """
    Schema for POST /api/quiz/proceed: explicitly advances the quiz from
    synopsis/characters to baseline question generation without requiring
    a pseudo 'answer'.
    """
    quiz_id: UUID


# ---------------------------------------------------------------------------
# Models for Asynchronous Status Polling (GET /api/quiz/status/{quizId})
# ---------------------------------------------------------------------------
class ProcessingResponse(APIBaseModel):
    """Schema for when the agent is still processing in the background."""
    status: Literal["processing"]
    quiz_id: UUID


class QuizStatusQuestion(APIBaseModel):
    """Schema for when the status poll returns a new question."""
    status: Literal["active"]
    type: Literal["question"]
    data: Question


# Single source of truth for the final result schema
class FinalResult(APIBaseModel):
    """Schema for the final, generated result of a quiz."""
    title: str
    # Made optional to prevent hard failures when image generation is skipped/unavailable
    image_url: Optional[str] = None
    description: str


class QuizStatusResult(APIBaseModel):
    """Schema for when the status poll returns the final result."""
    status: Literal["finished"]
    type: Literal["result"]
    data: FinalResult


# A discriminated union for the different possible status responses.
QuizStatusResponse = Union[QuizStatusQuestion, QuizStatusResult, ProcessingResponse]


# ---------------------------------------------------------------------------
# Models for Results and Feedback
# ---------------------------------------------------------------------------
class ShareableResultResponse(APIBaseModel):
    """Schema for shareable quiz results that can be viewed by anyone with the link."""
    title: str = Field(..., description="The title of the quiz result")
    description: str = Field(..., description="The personalized result description")
    # Optional to avoid blocking shares when no image is available
    image_url: Optional[str] = Field(None, description="URL of the character image (optional)")
    category: Optional[str] = Field(None, description="The original quiz category")
    created_at: Optional[str] = Field(None, description="When the quiz was completed")


class FeedbackRequest(APIBaseModel):
    """Schema for submitting user feedback on a quiz result."""
    quiz_id: UUID = Field(..., description="The unique identifier for the quiz session")
    rating: FeedbackRatingEnum = Field(..., description="User's rating (up or down)")
    text: Optional[str] = Field(None, description="Optional feedback text")


# ---------------------------------------------------------------------------
# Model for Redis Cache Serialization
# ---------------------------------------------------------------------------

class PydanticGraphState(APIBaseModel):
    """
    A Pydantic model that mirrors the agent's GraphState TypedDict.
    This is used for reliable JSON serialization when caching the state in Redis.
    """
    # LangChain messages are complex objects; storing them as dicts is safer
    # for JSON serialization.
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    # Session identifiers and user input
    session_id: UUID
    trace_id: str
    category: str

    # Agent control flow
    error_count: int = 0

    # Retrieved and generated content (forward refs to state models)
    rag_context: Optional[List[Dict[str, Any]]] = None
    category_synopsis: Optional["Synopsis"] = None
    ideal_archetypes: List[str] = Field(default_factory=list)
    generated_characters: List["CharacterProfile"] = Field(default_factory=list)
    generated_questions: List["QuizQuestion"] = Field(default_factory=list)
    final_result: Optional[FinalResult] = None
