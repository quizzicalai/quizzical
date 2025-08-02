"""
Database Models (SQLAlchemy ORM)

This module defines the SQLAlchemy ORM models that directly correspond to the
tables and columns in the PostgreSQL database. These models are the "source of truth"
for data persistence and structure.

- Base: The declarative base class for all models.
- Character: The canonical knowledge base of all potential quiz outcomes.
- SessionHistory: The historical record of every completed quiz session,
                  serving as the primary source for the RAG process.
- CharacterSessionMap: A many-to-many join table linking characters to the
                       sessions they were involved in.
"""

import enum
import uuid
from typing import List

from sqlalchemy import (
    TIMESTAMP,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Import the Vector type from pgvector for SQLAlchemy
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    """The base class for all SQLAlchemy ORM models."""

    pass


# Define an Enum for user sentiment values to enforce constraints at the DB level
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
        ForeignKey("characters.id"),
        primary_key=True,
    ),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("session_history.session_id"),
        primary_key=True,
    ),
)


class Character(Base):
    """
    Represents a canonical character profile in the database. This table acts as
    the long-term, curated knowledge base of all possible quiz outcomes.
    """

    __tablename__ = "characters"

    # --- Primary Key ---
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Core Content ---
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

    # --- Quality Assurance & Learning ---
    judge_quality_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_quality_score >= 1 AND judge_quality_score <= 10"),
        nullable=True,
    )
    judge_feedback: Mapped[str] = mapped_column(Text, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # --- Relationships ---
    sessions: Mapped[List["SessionHistory"]] = relationship(
        secondary=character_session_map, back_populates="characters"
    )

    def __repr__(self) -> str:
        return f"<Character(id={self.id}, name='{self.name}')>"


class SessionHistory(Base):
    """
    Represents the historical record of a single, completed quiz session.
    This table is the primary source for the RAG process and for analyzing
    the agent's performance over time.
    """

    __tablename__ = "session_history"

    # --- Primary Key ---
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # --- User Input & AI Analysis ---
    category: Mapped[str] = mapped_column(
        Text, CheckConstraint("category <> ''"), nullable=False
    )
    category_synopsis: Mapped[str] = mapped_column(
        Text, CheckConstraint("category_synopsis <> ''"), nullable=False
    )

    # The vector embedding of the synopsis, used for semantic search.
    # The dimension (384) must match the output of the sentence-transformer model.
    synopsis_embedding: Mapped[List[float]] = mapped_column(Vector(384), nullable=False)

    # --- Agent & Session Data ---
    agent_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    session_transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    final_result: Mapped[str] = mapped_column(
        Text, CheckConstraint("final_result <> ''"), nullable=False
    )

    # --- Quality Assurance & Feedback ---
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

    # --- Timestamps ---
    created_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # --- Relationships ---
    characters: Mapped[List["Character"]] = relationship(
        secondary=character_session_map, back_populates="sessions"
    )

    def __repr__(self) -> str:
        return f"<SessionHistory(session_id={self.session_id}, category='{self.category}')>"
