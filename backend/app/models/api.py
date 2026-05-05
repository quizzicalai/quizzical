# backend/app/models/api.py
from __future__ import annotations

import enum
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.agent.schemas import QuestionAnswer

"""
API Models (Pydantic Schemas)

This module defines the Pydantic models used for API request and response
validation. It intentionally avoids importing from the agent layer to prevent
circular imports. Agent-side code is free to import types from here.

Surgical changes for FE alignment:
- Keep snake_case field names in Python, but expose camelCase via alias_generator.
- Add discriminators (`type`) where FE selects union variants.
- Ensure response models are used by endpoints (FastAPI will dump by_alias).
"""

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
    # Discriminator so it can participate in a discriminated union
    type: Literal["synopsis"] = "synopsis"
    title: str
    summary: str


class CharacterProfile(APIBaseModel):
    name: str
    short_description: str
    profile_text: str
    image_url: str | None = None


class AnswerOption(APIBaseModel):
    text: str
    image_url: str | None = None


class Question(APIBaseModel):
    # Shape expected by the FE when serving active questions
    text: str
    image_url: str | None = None
    options: list[AnswerOption]
    # Short status string shown in the upper-right of the FE quiz UI in place
    # of "% complete" / "Question N of M". May be omitted; the FE then renders
    # an empty pill rather than misleading progress text.
    progress_phrase: str | None = None
    # 1-based question number ("Question 14") shown at the bottom of the FE
    # quiz card. Optional so older clients/snapshots remain valid.
    question_number: int | None = None


# Internal/editorial question shape retained for compatibility with agent state
class QuizQuestion(APIBaseModel):
    # Discriminator so it can participate in a discriminated union if needed
    type: Literal["question"] = "question"
    question_text: str
    # typically [{"text": "...", "image_url": "..."}] but image key is optional
    options: list[dict[str, str]]
    # Optional status phrase persisted alongside the question so the same value
    # surfaces every time the FE polls (avoids the phrase changing under the
    # user on a re-render).
    progress_phrase: str | None = None


class FinalResult(APIBaseModel):
    """Authoritative final result schema (imported by tools and agent)."""
    title: str
    image_url: str | None = None
    description: str


# -----------------------------------------------------------------------------
# Requests / Responses for quiz flows
# -----------------------------------------------------------------------------
class StartQuizRequest(APIBaseModel):
    category: str = Field(..., min_length=3, max_length=100)
    cf_turnstile_response: str = Field(..., alias="cf-turnstile-response")

    @field_validator("category", mode="before")
    @classmethod
    def _harden_category(cls, v: Any) -> Any:
        """§15.3 — reject control chars / bidi overrides / NUL / oversized UTF-8."""
        if not isinstance(v, str):
            return v
        # AC-IN-3: NUL byte
        if "\x00" in v:
            raise ValueError("category must not contain null bytes")
        # AC-IN-1: C0 (0–31 except \t) and C1 (127–159) controls
        for ch in v:
            cp = ord(ch)
            if cp == 9:  # tab is allowed and will be normalized to space
                continue
            if cp < 32 or 127 <= cp <= 159:
                raise ValueError("category must not contain control characters")
        # AC-IN-2: bidi-override codepoints
        forbidden_bidi = {
            0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
            0x2066, 0x2067, 0x2068, 0x2069,
        }
        if any(ord(ch) in forbidden_bidi for ch in v):
            raise ValueError("category must not contain Unicode bidi-override characters")
        # AC-IN-6: normalize whitespace
        normalized = " ".join(v.split())
        # AC-IN-5: empty after stripping
        if not normalized:
            raise ValueError("category must not be empty")
        # AC-IN-4: 400-byte UTF-8 cap
        if len(normalized.encode("utf-8")) > 400:
            raise ValueError("category exceeds maximum byte length (400)")
        return normalized


# For initial payload we allow either a synopsis or a "question-like" object.
# Make this a discriminated union on `type`.
DataUnion = Annotated[
    Synopsis | QuizQuestion,
    Field(discriminator="type"),
]


class StartQuizPayload(APIBaseModel):
    # Keep this for external clarity; internal routing uses data.type
    type: Literal["synopsis", "question"]
    data: DataUnion


class CharactersPayload(APIBaseModel):
    type: Literal["characters"] = "characters"
    data: list[CharacterProfile]


class FrontendStartQuizResponse(APIBaseModel):
    quiz_id: UUID
    initial_payload: StartQuizPayload | None = None
    characters_payload: CharactersPayload | None = None


class NextQuestionRequest(APIBaseModel):
    quiz_id: UUID
    question_index: int = Field(ge=0)
    # Free-text answer is capped at 2 KB. The UI sends short multiple-choice
    # text; anything larger is misuse and would otherwise bloat the agent
    # state in Redis and pollute structured logs.
    answer: str | None = Field(default=None, max_length=2048)
    option_index: int | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _require_answer_or_option(self) -> "NextQuestionRequest":
        # At least one of `answer` (non-empty after strip) or `option_index`
        # must be provided so the route never records an empty answer.
        has_text = isinstance(self.answer, str) and self.answer.strip() != ""
        has_index = self.option_index is not None
        if not (has_text or has_index):
            raise ValueError(
                "must provide either a non-empty `answer` or an `option_index`"
            )
        return self


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


class CharacterImage(APIBaseModel):
    """One name/url pair from the asynchronously-generated character image set."""
    name: str
    image_url: str | None = None


class QuizMediaResponse(APIBaseModel):
    """Snapshot of asynchronously-generated images for a quiz session.

    Image generation runs as a background task after `/quiz/start` returns and
    persists URLs to Postgres only. The frontend polls this endpoint while a
    session is on the synopsis screen to surface images as they become
    available, without blocking the user-visible flow.
    """
    quiz_id: UUID
    synopsis_image_url: str | None = None
    result_image_url: str | None = None
    characters: list[CharacterImage] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Public result & feedback
# -----------------------------------------------------------------------------
class ShareableResultResponse(APIBaseModel):
    title: str
    description: str
    image_url: str | None = None
    category: str | None = None
    created_at: str | None = None


class FeedbackRatingEnum(str, enum.Enum):
    UP = "up"
    DOWN = "down"


class FeedbackRequest(APIBaseModel):
    quiz_id: UUID
    rating: FeedbackRatingEnum
    # Free-text comment from end users — capped at 4 KB to prevent log
    # poisoning, DB row bloat, and accidental dumps of huge payloads. The
    # FE has its own UI cap; this is the server-side defense in depth.
    text: str | None = Field(default=None, max_length=4096)


# -----------------------------------------------------------------------------
# Optional: serialized graph state (kept loose to avoid importing agent state)
# -----------------------------------------------------------------------------
class PydanticGraphState(APIBaseModel):
    """
    A serialization-friendly view of the agent state for persistence (e.g., Redis).
    Types are intentionally generic to avoid import cycles.
    """
    # Preserve unknown/extra keys written by the agent (e.g., gating flags)
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="allow",  # <<< ensure fields like ready_for_questions/baseline_count are not dropped
    )

    messages: list[dict[str, Any]] = Field(default_factory=list)

    session_id: UUID
    trace_id: str
    category: str

    error_count: int = 0
    # Keep parity with agent state so error flags/messages persist round-trips
    is_error: bool = False  # <<< added
    error_message: str | None = None  # <<< added

    rag_context: list[dict[str, Any]] | None = None
    # Keep the typing as Synopsis to preserve editor help; at runtime the agent
    # may still place a plain dict here — the endpoint normalizes it.
    synopsis: Synopsis | None = None
    ideal_archetypes: list[str] = Field(default_factory=list)
    generated_characters: list[CharacterProfile] = Field(default_factory=list)
    generated_questions: list[QuizQuestion] = Field(default_factory=list)
    final_result: FinalResult | None = None
    quiz_history: list[QuestionAnswer] = Field(default_factory=list)

    # Gating & coordination fields needed by the router/endpoints
    baseline_count: int = 0  # <<< added
    ready_for_questions: bool = False  # <<< added
    last_served_index: int | None = None  # <<< added
