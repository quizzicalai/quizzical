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
    Integer,
    LargeBinary,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    sql,
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
    profile_picture: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # AC-DB-ORM-1: image_url mirrors the column added by db/init/init.sql ALTER
    # (§7.8 FAL pipeline). Required so ORM reads surface FAL-generated URLs
    # rather than silently dropping them.
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- §21.3 additive precompute columns (Phase 1) ---------------------
    # AC-PRECOMP-DEDUP-1: canonical key drives cross-topic character reuse.
    canonical_key: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # Optional embedding for cross-pack consistency (`AC-PRECOMP-QUAL-7`).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    flag_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    image_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Optional quality fields (populated by judge/evaluation flows)
    judge_quality_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_quality_score >= 1 AND judge_quality_score <= 10"),
        nullable=True,
    )
    judge_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Reverse many-to-many (sessions that used this character)
    sessions: Mapped[list["SessionHistory"]] = relationship(
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
    synopsis_embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)

    # Optional agent planning/explanations for later analysis
    agent_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
    final_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Judge/evaluation (optional)
    judge_plan_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("judge_plan_score >= 1 AND judge_plan_score <= 10"),
        nullable=True,
    )
    judge_plan_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User feedback (optional)
    user_sentiment: Mapped[UserSentimentEnum | None] = mapped_column(
        SAEnum(UserSentimentEnum, name="user_sentiment_enum"), nullable=True
    )
    user_feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Completion flags & QA history
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql.false())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_history: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Many-to-many linkage to characters used in the session
    characters: Mapped[list["Character"]] = relationship(
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
    baseline_questions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    adaptive_questions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SessionQuestions session_id={self.session_id}>"


# ===========================================================================
# §21 — Pre-Computed Topic Knowledge Packs (Phase 1: schema only)
# ===========================================================================
#
# These models implement the §21.3 domain model described in
# specifications/backend-design.MD. They are additive — no existing table is
# rewritten. Application code does NOT yet read or write these tables; that
# arrives in Phase 2 (read-path shim) behind `precompute.enabled=False`.
#
# Every column listed here has a matching `CREATE TABLE IF NOT EXISTS` /
# `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` block in db/init/init.sql so the
# production Postgres init stays in lock-step with the ORM (the only source
# of truth for the SQLite test bench).


class Topic(Base):
    """Canonical topic identity (§21.3)."""
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(
        Text, CheckConstraint("display_name <> ''"), nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    popularity_rank: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    current_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    flag_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TopicAlias(Base):
    """Alias → topic many-to-one mapping (§21.3)."""
    __tablename__ = "topic_aliases"

    alias_normalized: Mapped[str] = mapped_column(Text, primary_key=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_alias: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Synopsis(Base):
    """Canonical synopsis body, deduped by `content_hash` (§21.3)."""
    __tablename__ = "synopses"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    image_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    evaluator_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    flag_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CharacterSet(Base):
    """A reusable composition of characters, deduped by `composition_hash`."""
    __tablename__ = "character_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    composition_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    composition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BaselineQuestionSet(Base):
    """A reusable baseline-question composition, deduped by hash."""
    __tablename__ = "baseline_question_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    composition_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    composition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Question(Base):
    """Canonical question text, deduped by `text_hash` (§21.3)."""
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    text_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # baseline | adaptive
    image_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    requires_factual_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql.false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaAsset(Base):
    """Image / media asset, deduped by `content_hash` (§21.3 + §21.10)."""
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    content_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False, index=True
    )
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    storage_provider: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fal'")
    )
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    bytes_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    prompt_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    flag_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    pending_rehost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """§21 Phase 12 — set when blob upload deferred to async worker."""
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TopicPack(Base):
    """A versioned bundle binding one topic to one synopsis / charset / qset."""
    __tablename__ = "topic_packs"
    __table_args__ = (
        # AC-PRECOMP-BUILD: monotonic version per topic.
        UniqueConstraint("topic_id", "version", name="uq_topic_packs_topic_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    synopsis_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("synopses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    character_set_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("character_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    baseline_question_set_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("baseline_question_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evaluator_score: Mapped[int | None] = mapped_column(
        SmallInteger,
        CheckConstraint("evaluator_score >= 1 AND evaluator_score <= 10"),
        nullable=True,
    )
    evaluator_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cost_cents: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    built_in_env: Mapped[str] = mapped_column(Text, nullable=False)


class ContentFlag(Base):
    """User-submitted content flag (§21.8)."""
    __tablename__ = "content_flags"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrecomputeJob(Base):
    """Build/evaluate ledger row (§21.5)."""
    __tablename__ = "precompute_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_cents: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    evaluator_history: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    delayed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class QuizJob(Base):
    """Durable tracking of an in-flight live agent run for one quiz session.

    run_agent_in_background marks ``running`` (with a heartbeat) then
    ``succeeded``/``failed``. A recovery sweeper re-runs rows left ``running``
    with a stale heartbeat — i.e. the worker process died mid-run (deploy / OOM
    / Container Apps scale-in) — so a quiz is never permanently stuck
    ``processing``.
    """

    __tablename__ = "quiz_jobs"

    quiz_id: Mapped[uuid.UUID] = mapped_column(SAUUID(as_uuid=True), primary_key=True)
    phase: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'agent'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvaluatorTrainingExample(Base):
    """Operator-graded artefact captured for fine-tune / golden set (§21.6.1)."""
    __tablename__ = "evaluator_training_examples"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    artefact_kind: Mapped[str] = mapped_column(Text, nullable=False)
    artefact_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    operator_score: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("operator_score >= 1 AND operator_score <= 10"),
        nullable=False,
    )
    operator_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmbeddingsCache(Base):
    """Deduplicated embedding store keyed by `text_hash` (`AC-PRECOMP-COST-1`)."""
    __tablename__ = "embeddings_cache"

    text_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """Append-only audit row written for every operator action (`AC-PRECOMP-FLAG-6`).

    Append-only is enforced at two layers:
      - Application: no DAO function exposes an UPDATE on this model.
      - Database (Postgres): the production `init.sql` `REVOKE`s UPDATE/DELETE
        on this table from the application role. SQLite tests cover only the
        application layer.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    before_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Q&A icon enrichment (DRAFT — behind quizzical.images.qa_icons_enabled, off
# by default). Brand-recolored icon library + 384-dim caption embedding. The
# build-time binder (app/services/icons/binder.py) resolves a Q&A string to an
# icon id via vector NN over `embedding`, mirroring lookup.py::_vector_nn.
#
# ADDITIVE: this table is never referenced by any existing read/write path; it
# is only touched when the flag is ON. The vector shape (Vector(384)) + IVFFlat
# cosine index (lists=100, Postgres-only) match topics.embedding exactly. The
# matching DDL lives in backend/db/init/init.sql.
# ---------------------------------------------------------------------------

class IconAsset(Base):
    """Brand-recolored icon + rich caption + 384-dim embedding (same space as
    ``topics.embedding`` / ``embeddings_cache.embedding``)."""
    __tablename__ = "icon_assets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # stable icon id, e.g. 'rocket'
    lucide_name: Mapped[str] = mapped_column(Text, nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    palette_variant: Mapped[str] = mapped_column(Text, nullable=False)
    source_set: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'lucide'")
    )
    license: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'ISC'")
    )
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# FAL spend ledger (DRAFT — supports quizzical.images.qa_generated_images_enabled,
# OFF by default). Persistent, append-only record of every FAL image generation
# attempt + its cost, used to ENFORCE a hard lifetime $-cap before any FAL call.
#
# This is the cost guardrail prior reviews flagged as missing: the existing
# `precompute_jobs.cost_cents` tracks per-day LLM build spend, but there was no
# durable, lifetime FAL-only ledger. Spend persists across processes / deploys,
# so the owner's budget cannot be overrun by repeated builds.
#
# ADDITIVE: never referenced by any existing read/write path; only the
# same-universe generation pipeline touches it (and only when the flag is ON).
# Matching DDL lives in backend/db/init/init.sql.
# ---------------------------------------------------------------------------

class FalSpendLedger(Base):
    """One row per FAL generation attempt charged against the lifetime cap.

    ``cost_micros`` is the AUTHORITATIVE spend unit: micro-cents (1 cent =
    1000 micros), so a $0.011 = 1.1¢ image is recorded EXACTLY as ``1100`` with
    no per-row rounding loss (the lifetime SUM therefore reflects true FAL
    spend, and the $150 cap is real). ``cost_cents`` is a derived, human-readable
    mirror (``round(cost_micros / 1000)``) kept for audit / existing dashboards;
    it is NOT used for the cap math. A charge is recorded ONLY when FAL actually
    made a billable generate call. ``status`` is 'charged' for a real spend,
    'reused' for a dedup-skipped call (cost 0, kept for auditability), or
    'blocked' for an attempt the cap refused (cost 0)."""

    __tablename__ = "fal_spend_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. 'qa_image'
    topic_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    fal_request_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_micros: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cost_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'charged'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, server_default=func.now()
    )


class FalSpendCounter(Base):
    """One lock row per ``purpose`` used to serialise the FAL cap check+record.

    ``guarded_generate`` takes ``SELECT ... FOR UPDATE`` on this row so two
    concurrent builds cannot both read an under-cap SUM and then both insert,
    overshooting the lifetime cap. The row carries no authoritative total (the
    SUM over ``fal_spend_ledger.cost_micros`` is the source of truth); it exists
    purely as the lock target. Harmless no-op under sqlite (single-process)."""

    __tablename__ = "fal_spend_counter"

    purpose: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. 'qa_image'
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


