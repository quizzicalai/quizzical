# backend/app/models/db.py
"""
Database Models (SQLAlchemy ORM)

These ORM models map directly to PostgreSQL tables. They are intentionally
minimal and stable because many parts of the system (RAG, persistence,
analytics) depend on their shape.

Notes:
- `SessionHistory.synopsis_embedding` uses pgvector (nullable) so we can persist
  sessions even when embeddings are unavailable or deferred.
- An IVFFLAT index (cosine) is declared to accelerate similarity search as data
  grows. For local/dev datasets, a sequential scan is fine.
"""

from __future__ import annotations

import enum
import uuid
from typing import List

from sqlalchemy import (
    TIMESTAMP,
    UUID,
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


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

# Many-to-many relationship between sessions and characters
character_session_map = Table(
    "character_session_map",
    Base.metadata,
    Column(
        "character_id",
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("session_history.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------

class Character(Base):
    """
    Canonical character profile (long-lived outcomes).
    """
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
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
    profile_picture: Mapped[bytes] = mapped_column(BYTEA, nullable=True)

    # Optional quality fields (populated by judge/evaluation flows)
    judge_quality_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_quality_score >= 1 AND judge_quality_score <= 10"),
        nullable=True,
    )
    judge_feedback: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Reverse many-to-many (sessions that used this character)
    sessions: Mapped[List["SessionHistory"]] = relationship(
        secondary=character_session_map,
        back_populates="characters",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - repr is for debugging
        return f"<Character id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# SessionHistory
# ---------------------------------------------------------------------------

class SessionHistory(Base):
    """
    Historical record of a single quiz session. This is the primary source
    for RAG and analytics. Most fields are JSONB to retain structure while
    keeping the schema stable.
    """
    __tablename__ = "session_history"

    # Primary identifier for a quiz session
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # Original user-provided category/topic (kept simple for filtering)
    category: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("category <> ''"),
        nullable=False,
    )

    # Structured synopsis object (e.g., {"title": "...", "summary": "..."})
    category_synopsis: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Nullable vector so persistence never blocks without embeddings
    # Dimension 384 aligns with default HF model in llm_service.
    synopsis_embedding: Mapped[List[float]] = mapped_column(Vector(384), nullable=True)

    # Optional agent planning/explanations for later analysis
    agent_plan: Mapped[dict] = mapped_column(JSONB, nullable=True)

    # Transcript of the session (list of message dicts)
    # NOTE: This was previously typed as `dict`; JSONB supports arrays, and the
    # rest of the app treats this as a list. Type hint only—no migration needed.
    session_transcript: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Final result object (e.g., {"title": "...", "description": "...", "image_url": "..."})
    final_result: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Optional judge/evaluation metadata
    judge_plan_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_plan_score >= 1 AND judge_plan_score <= 10"),
        nullable=True,
    )
    judge_plan_feedback: Mapped[str] = mapped_column(Text, nullable=True)

    # Optional user sentiment + feedback
    user_sentiment: Mapped[UserSentimentEnum] = mapped_column(
        Enum(UserSentimentEnum, name="user_sentiment_enum"),
        nullable=True,
    )
    user_feedback_text: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Many-to-many linkage to characters used in the session
    characters: Mapped[List["Character"]] = relationship(
        secondary=character_session_map,
        back_populates="sessions",
        passive_deletes=True,
    )

    # IVFFLAT index using cosine distance on the vector column.
    # Safe to keep declared here; Postgres will only apply it if pgvector is installed
    # and the index exists (otherwise migrations handle creation).
    __table_args__ = (
        Index(
            "idx_session_synopsis_embedding_cosine_ivf",
            "synopsis_embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"synopsis_embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - repr is for debugging
        return f"<SessionHistory session_id={self.session_id} category={self.category!r}>"
