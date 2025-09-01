# backend/app/models/db.py
"""
Database Models (SQLAlchemy ORM)

This module defines the SQLAlchemy ORM models that directly correspond to the
tables and columns in the PostgreSQL database.

Notes for RAG:
- `SessionHistory.synopsis_embedding` is a nullable pgvector column so sessions
  are persisted even if embedding generation fails or is deferred.
- An optional IVF index is declared for cosine distance to accelerate similarity
  search as data grows. For tiny local datasets a seq scan is fine.
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


class Base(DeclarativeBase):
    """The base class for all SQLAlchemy ORM models."""
    pass


class UserSentimentEnum(enum.Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NONE = "NONE"


# Association table for the many-to-many relationship between
# SessionHistory and Character.
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


class Character(Base):
    """
    Represents a canonical character profile in the database. This table acts as
    the long-term, curated knowledge base of all possible quiz outcomes.
    """
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        Text, CheckConstraint("name <> ''"), unique=True, nullable=False, index=True
    )
    short_description: Mapped[str] = mapped_column(
        Text, CheckConstraint("short_description <> ''"), nullable=False
    )
    profile_text: Mapped[str] = mapped_column(
        Text, CheckConstraint("profile_text <> ''"), nullable=False
    )
    profile_picture: Mapped[bytes] = mapped_column(BYTEA, nullable=True)
    judge_quality_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_quality_score >= 1 AND judge_quality_score <= 10"),
        nullable=True,
    )
    judge_feedback: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    sessions: Mapped[List["SessionHistory"]] = relationship(
        secondary=character_session_map, back_populates="characters", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Character(id={self.id}, name='{self.name}')>"


class SessionHistory(Base):
    """
    Represents the historical record of a single, completed quiz session.
    This table is the primary source for the RAG process.
    """
    __tablename__ = "session_history"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    category: Mapped[str] = mapped_column(
        Text, CheckConstraint("category <> ''"), nullable=False
    )
    category_synopsis: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Nullable vector so persistence never blocks when embeddings are unavailable.
    synopsis_embedding: Mapped[List[float]] = mapped_column(Vector(384), nullable=True)

    agent_plan: Mapped[dict] = mapped_column(JSONB, nullable=True)
    session_transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    final_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    judge_plan_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_plan_score >= 1 AND judge_plan_score <= 10"),
        nullable=True,
    )
    judge_plan_feedback: Mapped[str] = mapped_column(Text, nullable=True)
    user_sentiment: Mapped[UserSentimentEnum] = mapped_column(
        Enum(UserSentimentEnum, name="user_sentiment_enum"), nullable=True
    )
    user_feedback_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    characters: Mapped[List["Character"]] = relationship(
        secondary=character_session_map, back_populates="sessions", passive_deletes=True
    )

    # Optional IVF index for cosine distance (speeds up ANN as data grows).
    # Safe to keep here; for tiny datasets a sequential scan is fine.
    __table_args__ = (
        Index(
            "idx_session_synopsis_embedding_cosine_ivf",
            "synopsis_embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"synopsis_embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<SessionHistory(session_id={self.session_id}, category='{self.category}')>"
