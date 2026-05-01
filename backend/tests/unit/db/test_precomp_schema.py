# backend/tests/unit/db/test_precomp_schema.py
"""
Phase 1 — Pre-Computed Topic Knowledge Packs (§21.3) — schema tests.

Source of truth: specifications/backend-design.MD §21.3 and the
implementation plan in specifications/backend-implementation-plan.MD Phase 1.

These tests exercise the new ORM models against the in-memory SQLite engine
provided by `tests/fixtures/db_fixtures.py::sqlite_db_session`. They form the
write-side gate for Phase 1: when these all pass and the rest of the suite is
unaffected, the schema phase is complete.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    AuditLog,
    BaselineQuestionSet,
    Character,
    CharacterSet,
    ContentFlag,
    EmbeddingsCache,
    EvaluatorTrainingExample,
    MediaAsset,
    PrecomputeJob,
    Question,
    Synopsis,
    Topic,
    TopicAlias,
    TopicPack,
)
from app.services.precompute.canonicalize import canonical_key_for_name

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Universal — Base.metadata.create_all reaches every new table
# ---------------------------------------------------------------------------

async def test_orm_metadata_creates_all_new_tables(sqlite_db_session: AsyncSession):
    """All §21.3 tables exist on the SQLite test engine after create_all."""
    expected = {
        "topics",
        "topic_aliases",
        "topic_packs",
        "synopses",
        "character_sets",
        "baseline_question_sets",
        "questions",
        "media_assets",
        "content_flags",
        "precompute_jobs",
        "evaluator_training_examples",
        "embeddings_cache",
        "audit_log",
    }

    bind = sqlite_db_session.bind

    def _check(sync_conn):
        insp = inspect(sync_conn)
        return set(insp.get_table_names())

    actual = await sqlite_db_session.run_sync(lambda s: _check(s.connection()))
    missing = expected - actual
    assert not missing, f"Missing precompute tables: {sorted(missing)}"


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------

async def test_topics_slug_unique_constraint(sqlite_db_session: AsyncSession):
    a = Topic(slug="star-wars", display_name="Star Wars")
    b = Topic(slug="star-wars", display_name="Star Wars (alt)")
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    sqlite_db_session.add(b)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


async def test_topics_policy_status_default_allowed(sqlite_db_session: AsyncSession):
    t = Topic(slug="greek-mythology", display_name="Greek Mythology")
    sqlite_db_session.add(t)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(t)
    assert t.policy_status == "allowed"
    assert t.flag_count == 0


# ---------------------------------------------------------------------------
# topic_aliases
# ---------------------------------------------------------------------------

async def test_topic_aliases_pk_alias_normalized_topic_id(
    sqlite_db_session: AsyncSession,
):
    t = Topic(slug="the-office", display_name="The Office")
    sqlite_db_session.add(t)
    await sqlite_db_session.flush()

    a1 = TopicAlias(
        alias_normalized="the office",
        topic_id=t.id,
        display_alias="The Office",
    )
    a2 = TopicAlias(
        alias_normalized="the office",
        topic_id=t.id,
        display_alias="The Office (US)",
    )
    sqlite_db_session.add(a1)
    await sqlite_db_session.commit()
    sqlite_db_session.add(a2)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


# ---------------------------------------------------------------------------
# topic_packs
# ---------------------------------------------------------------------------

async def _seed_pack_dependencies(
    session: AsyncSession,
) -> tuple[Topic, Synopsis, CharacterSet, BaselineQuestionSet]:
    """Helper: create the FK targets a TopicPack needs."""
    topic = Topic(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="Topic")
    session.add(topic)
    await session.flush()

    syn = Synopsis(
        topic_id=topic.id,
        content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        body={"title": "T", "summary": "S"},
    )
    cs = CharacterSet(
        composition_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        composition={"members": []},
    )
    qs = BaselineQuestionSet(
        composition_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        composition={"questions": []},
    )
    session.add_all([syn, cs, qs])
    await session.flush()
    return topic, syn, cs, qs


async def test_topic_packs_unique_topic_version(sqlite_db_session: AsyncSession):
    topic, syn, cs, qs = await _seed_pack_dependencies(sqlite_db_session)

    p1 = TopicPack(
        topic_id=topic.id,
        version=1,
        status="draft",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=qs.id,
        model_provenance={"synopsis": {"model": "test"}},
        built_in_env="local",
    )
    p2 = TopicPack(
        topic_id=topic.id,
        version=1,
        status="draft",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=qs.id,
        model_provenance={"synopsis": {"model": "test"}},
        built_in_env="local",
    )
    sqlite_db_session.add(p1)
    await sqlite_db_session.commit()
    sqlite_db_session.add(p2)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


async def test_topic_packs_cost_cents_default_zero(sqlite_db_session: AsyncSession):
    topic, syn, cs, qs = await _seed_pack_dependencies(sqlite_db_session)
    p = TopicPack(
        topic_id=topic.id,
        version=1,
        status="draft",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=qs.id,
        model_provenance={},
        built_in_env="local",
    )
    sqlite_db_session.add(p)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(p)
    assert p.cost_cents == 0
    assert p.published_at is None


# ---------------------------------------------------------------------------
# synopses — content hash dedup
# ---------------------------------------------------------------------------

async def test_synopses_content_hash_unique(sqlite_db_session: AsyncSession):
    topic = Topic(slug="t1", display_name="T1")
    sqlite_db_session.add(topic)
    await sqlite_db_session.flush()

    h = hashlib.sha256(b"same-body").hexdigest()
    a = Synopsis(topic_id=topic.id, content_hash=h, body={"title": "x"})
    b = Synopsis(topic_id=topic.id, content_hash=h, body={"title": "y"})
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    sqlite_db_session.add(b)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


# ---------------------------------------------------------------------------
# media_assets — prompt_hash dedup
# ---------------------------------------------------------------------------

async def test_media_assets_content_hash_unique(sqlite_db_session: AsyncSession):
    h = hashlib.sha256(b"image-bytes").hexdigest()
    a = MediaAsset(
        content_hash=h,
        prompt_hash=hashlib.sha256(b"p").hexdigest(),
        storage_provider="fal",
        storage_uri="https://example/a.png",
        prompt_payload={"prompt": "p"},
    )
    b = MediaAsset(
        content_hash=h,
        prompt_hash=hashlib.sha256(b"q").hexdigest(),
        storage_provider="fal",
        storage_uri="https://example/b.png",
        prompt_payload={"prompt": "q"},
    )
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    sqlite_db_session.add(b)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


# ---------------------------------------------------------------------------
# embeddings_cache
# ---------------------------------------------------------------------------

async def test_embeddings_cache_text_hash_unique(sqlite_db_session: AsyncSession):
    h = hashlib.sha256(b"hello world").hexdigest()
    a = EmbeddingsCache(text_hash=h, model="m", dim=384, embedding=[0.0] * 384)
    b = EmbeddingsCache(text_hash=h, model="m", dim=384, embedding=[1.0] * 384)
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    sqlite_db_session.add(b)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


# ---------------------------------------------------------------------------
# precompute_jobs default state
# ---------------------------------------------------------------------------

async def test_precompute_jobs_default_status_queued(sqlite_db_session: AsyncSession):
    topic = Topic(slug="job-topic", display_name="Job Topic")
    sqlite_db_session.add(topic)
    await sqlite_db_session.flush()
    j = PrecomputeJob(topic_id=topic.id)
    sqlite_db_session.add(j)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(j)
    assert j.status == "queued"
    assert j.attempt == 0
    assert j.cost_cents == 0


# ---------------------------------------------------------------------------
# audit_log — append-only at the application layer
# ---------------------------------------------------------------------------

async def test_audit_log_insert_and_query(sqlite_db_session: AsyncSession):
    a = AuditLog(
        actor_id="op-1",
        action="promote",
        target_kind="topic_pack",
        target_id=str(uuid.uuid4()),
        before_hash="b" * 64,
        after_hash="a" * 64,
    )
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    rows = (
        await sqlite_db_session.execute(
            select(AuditLog).where(AuditLog.action == "promote")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].before_hash == "b" * 64


# ---------------------------------------------------------------------------
# characters — additive precompute columns
# ---------------------------------------------------------------------------

async def test_characters_has_new_precomp_columns(sqlite_db_session: AsyncSession):
    """Character ORM exposes the §21.3 additive precompute columns."""
    char = Character(
        name="Test Char",
        short_description=".",
        profile_text=".",
        canonical_key="test char",
        evaluator_score=8,
        flag_count=0,
        policy_status="allowed",
    )
    sqlite_db_session.add(char)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(char)
    assert char.canonical_key == "test char"
    assert char.evaluator_score == 8
    assert char.flag_count == 0
    assert char.policy_status == "allowed"
    assert char.image_asset_id is None
    # `embedding` column is exposed; nullable.
    assert char.embedding is None


async def test_characters_canonical_key_helper_normalises(
    sqlite_db_session: AsyncSession,
):
    """Application-layer helper produces deterministic canonical keys."""
    assert canonical_key_for_name("Foo Bar") == "foo bar"
    assert canonical_key_for_name("  HÉCTOR  ") == "hector"
    assert canonical_key_for_name("R2-D2") == "r2-d2"


# ---------------------------------------------------------------------------
# Sanity — cross-pack relationships round-trip
# ---------------------------------------------------------------------------

async def test_topic_pack_round_trip(sqlite_db_session: AsyncSession):
    topic, syn, cs, qs = await _seed_pack_dependencies(sqlite_db_session)
    pack = TopicPack(
        topic_id=topic.id,
        version=1,
        status="draft",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=qs.id,
        evaluator_score=9,
        model_provenance={"synopsis": {"model": "gpt-test", "temp": 0.4}},
        cost_cents=42,
        built_in_env="local",
    )
    sqlite_db_session.add(pack)
    await sqlite_db_session.commit()

    fetched = (
        await sqlite_db_session.execute(
            select(TopicPack).where(TopicPack.id == pack.id)
        )
    ).scalar_one()
    assert fetched.evaluator_score == 9
    assert fetched.model_provenance["synopsis"]["model"] == "gpt-test"
    assert fetched.cost_cents == 42
    assert isinstance(fetched.built_at, datetime)


# ---------------------------------------------------------------------------
# Sanity — content_flags + question + evaluator_training_example smoke
# ---------------------------------------------------------------------------

async def test_content_flag_smoke(sqlite_db_session: AsyncSession):
    flag = ContentFlag(
        target_kind="synopsis",
        target_id=str(uuid.uuid4()),
        reason_code="incorrect",
        reason_text="Plot summary is wrong.",
        client_ip_hash="a" * 64,
    )
    sqlite_db_session.add(flag)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(flag)
    assert flag.created_at is not None


async def test_question_smoke(sqlite_db_session: AsyncSession):
    h = hashlib.sha256(b"q-text").hexdigest()
    q = Question(
        text_hash=h,
        text="What is your favourite colour?",
        options={"a": "Red", "b": "Blue"},
        kind="baseline",
    )
    sqlite_db_session.add(q)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(q)
    assert q.kind == "baseline"


async def test_evaluator_training_example_smoke(sqlite_db_session: AsyncSession):
    ex = EvaluatorTrainingExample(
        artefact_kind="synopsis",
        artefact_payload={"title": "T"},
        operator_score=9,
        operator_notes="ok",
    )
    sqlite_db_session.add(ex)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(ex)
    assert ex.operator_score == 9
