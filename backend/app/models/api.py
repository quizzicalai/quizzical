# backend/app/models/api.py
"""
API Models (Pydantic Schemas)

This module defines the Pydantic models used for API request and response
validation. These models act as the "contract" for the API, ensuring that
all data flowing into and out of the application is structured, typed, and
validated.
"""
import enum
from typing import List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.agent.state import QuizQuestion, Synopsis


class APIBaseModel(BaseModel):
    """
    A base model for all API schemas to configure camelCase conversion.
    This ensures the JSON API uses camelCase, while the Python code
    remains idiomatic with snake_case.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
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
    A container for the initial data sent to the frontend, which can be
    either a synopsis to show the user or the first question directly.
    """
    type: str
    data: Union[QuizQuestion, Synopsis]


class FrontendStartQuizResponse(APIBaseModel):
    """
    The response model for the /quiz/start endpoint that matches the
    frontend's expectations.
    """
    # FIX: Renamed field to quiz_id and removed the incorrect alias.
    # The APIBaseModel's `alias_generator` will automatically convert this
    # to `quizId` in the JSON response, matching the frontend's expectation.
    quiz_id: UUID
    initial_payload: Optional[StartQuizPayload] = None


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
    """Schema for the request body of the POST /api/quiz/next endpoint."""
    quiz_id: UUID
    answer: str


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


class FinalResult(APIBaseModel):
    """Schema for the final, generated result of a quiz."""
    title: str
    image_url: str
    description: str


class QuizStatusResult(APIBaseModel):
    """Schema for when the status poll returns the final result."""
    status: Literal["finished"]
    type: Literal["result"]
    data: FinalResult


# A discriminated union for the different possible status responses.
QuizStatusResponse = Union[QuizStatusQuestion, QuizStatusResult, ProcessingResponse]


# ---------------------------------------------------------------------------
# Models for Feedback and Sharing
# ---------------------------------------------------------------------------
class FeedbackRequest(APIBaseModel):
    """Schema for the request body of the POST /api/feedback endpoint."""
    quiz_id: UUID
    rating: FeedbackRatingEnum
    text: Optional[str] = Field(
        None, max_length=2000, description="Optional detailed text feedback."
    )
    cf_turnstile_response: str = Field(
        ...,
        alias="cf-turnstile-response",
        description="The validation token from the Cloudflare Turnstile widget.",
    )


class ShareableResultResponse(APIBaseModel):
    """Schema for the public GET /api/result/{session_id} endpoint."""
    title: str
    description: str
    image_url: str
