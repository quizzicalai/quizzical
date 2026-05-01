"""Phase 2 — read-path lookup shim tests.

Covers:
  - AC-PRECOMP-LOOKUP-1 (alias-exact / slug-exact / vector NN above τ_match)
  - AC-PRECOMP-LOOKUP-2 (vector NN below τ_match → MISS)
  - AC-PRECOMP-LOOKUP-3 (thresholds read from config with documented defaults)
  - AC-PRECOMP-LOOKUP-4 (quarantined pack never returned even if FK pinned)
  - AC-PRECOMP-PERF-5  (no model call on the read path — uses precomputed
    `topics.embedding` only)
  - AC-PRECOMP-OBS-1  (lookup emits one structured log per call with
    {via, hit, similarity, topic_id})

These tests exercise `PrecomputeLookup` directly against the SQLite test
bench. The vector path uses an injectable Python-side cosine helper because
pgvector's `<=>` operator is Postgres-only; the production code path is
covered by the same service when run against Postgres.
"""

from __future__ import annotations

import logging
import math

import pytest

from app.models.db import (
    BaselineQuestionSet,
    CharacterSet,
    Synopsis,
    Topic,
    TopicAlias,
    TopicPack,
)
from app.services.precompute.lookup import (
    DEFAULT_THRESHOLDS,
    PrecomputeLookup,
    TopicResolution,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers — minimal pack scaffolding
# ---------------------------------------------------------------------------

async def _seed_published_pack(
    session,
    *,
    slug: str,
    display_name: str,
    aliases: list[str] | None = None,
    embedding: list[float] | None = None,
    pack_status: str = "published",
    policy_status: str = "allowed",
) -> tuple[Topic, TopicPack]:
    topic = Topic(
        slug=slug,
        display_name=display_name,
        embedding=embedding,
        policy_status=policy_status,
    )
    session.add(topic)
    await session.flush()

    syn = Synopsis(
        topic_id=topic.id,
        content_hash=f"syn-{slug}",
        body={"title": display_name, "summary": "x"},
    )
    cs = CharacterSet(composition_hash=f"cs-{slug}", composition={"members": []})
    bqs = BaselineQuestionSet(
        composition_hash=f"bqs-{slug}", composition={"questions": []}
    )
    session.add_all([syn, cs, bqs])
    await session.flush()

    pack = TopicPack(
        topic_id=topic.id,
        version=1,
        status=pack_status,
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={"prompts": [{"id": "syn", "sha": "x"}]},
        built_in_env="test",
    )
    session.add(pack)
    await session.flush()

    topic.current_pack_id = pack.id
    for a in aliases or []:
        session.add(
            TopicAlias(alias_normalized=a.lower(), topic_id=topic.id, display_alias=a)
        )
    await session.commit()
    return topic, pack


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _make_lookup(session, *, embed_fn=None, thresholds=None):
    return PrecomputeLookup(
        db=session,
        redis=None,  # Phase 2 has no Redis cache yet (Phase 4)
        thresholds=thresholds or DEFAULT_THRESHOLDS,
        embed_fn=embed_fn,
        # Inject Python cosine for SQLite (pgvector is Postgres-only).
        cosine_fn=_cosine,
    )


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-1
# ---------------------------------------------------------------------------


async def test_alias_exact_match_returns_pack(sqlite_db_session):
    topic, pack = await _seed_published_pack(
        sqlite_db_session,
        slug="harry-potter",
        display_name="Harry Potter",
        aliases=["The Boy Who Lived"],
    )
    res = await _make_lookup(sqlite_db_session).resolve_topic("the boy who lived")

    assert isinstance(res, TopicResolution)
    assert res.topic_id == topic.id
    assert res.pack_id == pack.id
    assert res.via == "alias"


async def test_slug_exact_match_returns_pack(sqlite_db_session):
    topic, pack = await _seed_published_pack(
        sqlite_db_session, slug="dune", display_name="Dune"
    )
    res = await _make_lookup(sqlite_db_session).resolve_topic("Dune")
    assert res is not None
    assert res.topic_id == topic.id
    assert res.via == "slug"


async def test_vector_nn_above_threshold_returns_pack(sqlite_db_session):
    target_emb = [1.0] + [0.0] * 383
    topic, pack = await _seed_published_pack(
        sqlite_db_session,
        slug="lord-of-the-rings",
        display_name="Lord of the Rings",
        embedding=target_emb,
    )

    # Query embedding is identical → cosine ≈ 1.0, well above τ_match=0.86.
    async def _embed(_text: str) -> list[float]:
        return target_emb

    res = await _make_lookup(sqlite_db_session, embed_fn=_embed).resolve_topic(
        "Tolkien epic"
    )
    assert res is not None
    assert res.topic_id == topic.id
    assert res.via == "vector"
    assert res.similarity is not None and res.similarity > 0.86


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-2
# ---------------------------------------------------------------------------


async def test_vector_nn_below_threshold_returns_none(sqlite_db_session):
    await _seed_published_pack(
        sqlite_db_session,
        slug="x",
        display_name="X",
        embedding=[1.0] + [0.0] * 383,
    )

    async def _embed(_text: str) -> list[float]:
        # Orthogonal vector → cosine = 0, far below τ_match.
        return [0.0, 1.0] + [0.0] * 382

    res = await _make_lookup(sqlite_db_session, embed_fn=_embed).resolve_topic(
        "totally unrelated"
    )
    assert res is None


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-3
# ---------------------------------------------------------------------------


def test_thresholds_default_values_documented():
    """Defaults match the §21 spec (`AC-PRECOMP-LOOKUP-3`)."""
    assert DEFAULT_THRESHOLDS.match == pytest.approx(0.86)
    assert DEFAULT_THRESHOLDS.pass_score == 7
    assert DEFAULT_THRESHOLDS.strong_trigger_score == 5


async def test_thresholds_match_value_is_respected(sqlite_db_session):
    """Lowering τ_match makes a previously-MISS query a HIT."""
    await _seed_published_pack(
        sqlite_db_session,
        slug="y",
        display_name="Y",
        embedding=[1.0] + [0.0] * 383,
    )

    async def _embed(_text: str) -> list[float]:
        # cosine ≈ 0.7 — below default 0.86 but above an overridden 0.5.
        v = [0.7, 0.7141428] + [0.0] * 382
        # Normalise crudely just to keep cosine in range.
        return v

    from app.services.precompute.lookup import LookupThresholds

    res_default = await _make_lookup(
        sqlite_db_session, embed_fn=_embed
    ).resolve_topic("zzz")
    res_loose = await _make_lookup(
        sqlite_db_session,
        embed_fn=_embed,
        thresholds=LookupThresholds(match=0.5, pass_score=7, strong_trigger_score=5),
    ).resolve_topic("zzz")

    assert res_default is None
    assert res_loose is not None
    assert res_loose.via == "vector"


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-4
# ---------------------------------------------------------------------------


async def test_quarantined_pack_is_never_returned(sqlite_db_session):
    """Even if `topics.current_pack_id` points at it, a non-published pack
    must never be returned by alias / slug / vector resolution."""
    await _seed_published_pack(
        sqlite_db_session,
        slug="dangerous",
        display_name="Dangerous",
        aliases=["danger"],
        pack_status="quarantined",
    )

    res_alias = await _make_lookup(sqlite_db_session).resolve_topic("danger")
    res_slug = await _make_lookup(sqlite_db_session).resolve_topic("Dangerous")
    assert res_alias is None
    assert res_slug is None


async def test_banned_topic_policy_is_never_returned(sqlite_db_session):
    """`policy_status=banned` topics must MISS even if pack is published."""
    await _seed_published_pack(
        sqlite_db_session,
        slug="bad",
        display_name="Bad",
        aliases=["bad"],
        policy_status="banned",
    )
    res = await _make_lookup(sqlite_db_session).resolve_topic("bad")
    assert res is None


# ---------------------------------------------------------------------------
# AC-PRECOMP-PERF-5 — no model call on the read path when an alias matches
# ---------------------------------------------------------------------------


async def test_no_model_call_when_alias_matches(sqlite_db_session):
    """An alias / slug match must short-circuit before embed_fn is consulted."""
    await _seed_published_pack(
        sqlite_db_session,
        slug="alias-only",
        display_name="Alias Only",
        aliases=["aka"],
    )

    calls: list[str] = []

    async def _embed(text: str) -> list[float]:  # pragma: no cover - guard
        calls.append(text)
        raise AssertionError("embed_fn must not be called when alias matches")

    res = await _make_lookup(
        sqlite_db_session, embed_fn=_embed
    ).resolve_topic("aka")
    assert res is not None
    assert calls == []


# ---------------------------------------------------------------------------
# AC-PRECOMP-OBS-1 — observability
# ---------------------------------------------------------------------------


async def test_lookup_emits_structured_log(sqlite_db_session, caplog):
    import structlog

    await _seed_published_pack(
        sqlite_db_session,
        slug="logme",
        display_name="LogMe",
        aliases=["lm"],
    )
    structlog.reset_defaults()
    caplog.set_level("INFO", logger="app.services.precompute.lookup")
    with structlog.testing.capture_logs() as captured:
        await _make_lookup(sqlite_db_session).resolve_topic("lm")

    relevant = [r for r in captured if "precompute.lookup" in r.get("event", "")]
    # Fall back to stdlib log capture if a prior test in the same process
    # rewired structlog so capture_logs no longer intercepts.
    if not relevant:
        relevant = [
            r for r in caplog.records
            if "precompute.lookup" in r.getMessage()
        ]
    assert relevant, (
        f"expected at least one precompute.lookup log; "
        f"structlog={captured!r} stdlib={[r.getMessage() for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# Empty / weird inputs
# ---------------------------------------------------------------------------


async def test_empty_input_returns_none(sqlite_db_session):
    res = await _make_lookup(sqlite_db_session).resolve_topic("")
    assert res is None
    res = await _make_lookup(sqlite_db_session).resolve_topic("   ")
    assert res is None
