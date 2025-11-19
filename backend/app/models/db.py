"""
Database Models (SQLAlchemy ORM) — aligned with init.sql

Tables:
- characters
- session_history
- character_session_map (M:N)
- session_questions

Notes:
- Uses pgvector.Vector(384) for synopsis_embedding.
- JSONB columns match the initialization script.
- Indexes are created in init.sql; we do not redefine them here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    UUID as SAUUID,
)
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    Table,
    Text,
    func,
    text,  # for server_default on JSONB
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserSentimentEnum(enum.Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Association Tables
# ---------------------------------------------------------------------------

character_session_map = Table(
    "character_session_map",
    Base.metadata,
    Column(
        "character_id",
        SAUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "session_id",
        SAUUID(as_uuid=True),
        ForeignKey("session_history.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------

class Character(Base):
    """Canonical character profile (long-lived, unique by name)."""
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("name <> ''"),
        unique=True,
        nullable=False,
        index=True,
    )
    short_description: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("short_description <> ''"),
        nullable=False,
    )
    profile_text: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("profile_text <> ''"),
        nullable=False,
    )
    profile_picture: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # Optional quality fields (populated by judge/evaluation flows)
    judge_quality_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_quality_score >= 1 AND judge_quality_score <= 10"),
        nullable=True,
    )
    judge_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Reverse many-to-many (sessions that used this character)
    sessions: Mapped[List["SessionHistory"]] = relationship(
        secondary=character_session_map,
        back_populates="characters",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Character id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# SessionHistory
# ---------------------------------------------------------------------------

class SessionHistory(Base):
    """
    One row per quiz session. Stores synopsis, transcript, final result,
    feedback, completion flags, and links to characters used.
    """
    __tablename__ = "session_history"

    session_id: Mapped[uuid.UUID] = mapped_column(SAUUID(as_uuid=True), primary_key=True)

    # Original user-provided category/topic (simple filterable text)
    category: Mapped[str] = mapped_column(
        Text, CheckConstraint("category <> ''"), nullable=False
    )

    # Structured synopsis object (e.g., {"title": "...", "summary": "..."})
    category_synopsis: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Nullable vector so persistence never blocks without embeddings (dim must match model)
    synopsis_embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(384), nullable=True)

    # Optional agent planning/explanations for later analysis
    agent_plan: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Transcript of the session (list[dict])
    session_transcript: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Snapshot of the chosen character set (array of objects)
    # Matches init.sql: NOT NULL DEFAULT '[]'::jsonb
    character_set: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    # Final result object (may be NULL until quiz completes)
    final_result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Judge/evaluation (optional)
    judge_plan_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_plan_score >= 1 AND judge_plan_score <= 10"),
        nullable=True,
    )
    judge_plan_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # User feedback (optional)
    user_sentiment: Mapped[Optional[UserSentimentEnum]] = mapped_column(
        SAEnum(UserSentimentEnum, name="user_sentiment_enum"), nullable=True
    )
    user_feedback_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Completion flags & QA history
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_history: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Many-to-many linkage to characters used in the session
    characters: Mapped[List["Character"]] = relationship(
        secondary=character_session_map,
        back_populates="sessions",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SessionHistory session_id={self.session_id} category={self.category!r}>"


# ---------------------------------------------------------------------------
# SessionQuestions
# ---------------------------------------------------------------------------

class SessionQuestions(Base):
    """
    Exactly one row per session; baseline/adaptive questions and any
    auxiliary properties are stored as JSON blobs.
    """
    __tablename__ = "session_questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("session_history.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    baseline_questions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    adaptive_questions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SessionQuestions session_id={self.session_id}>"
