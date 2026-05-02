"""§21 Phase 9 — `scripts/import_packs.py` acceptance tests."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.models.db import TopicPack
from scripts.import_packs import (
    UnsignedArchiveError,
    import_archive,
    sign_archive,
)

SECRET = "phase9-test-secret-" + "x" * 32


def _make_archive(topic_slug: str = "starter-1") -> bytes:
    doc = {
        "packs": [
            {
                "topic": {"slug": topic_slug, "display_name": topic_slug.title()},
                "synopsis": {
                    "content_hash": "syn-" + uuid.uuid4().hex,
                    "body": {"text": "starter synopsis"},
                },
                "character_set": {
                    "composition_hash": "cs-" + uuid.uuid4().hex,
                    "composition": {"character_ids": []},
                },
                "baseline_question_set": {
                    "composition_hash": "bqs-" + uuid.uuid4().hex,
                    "composition": {"question_ids": []},
                },
                "version": 1,
                "built_in_env": "starter",
            }
        ]
    }
    return json.dumps(doc).encode("utf-8")


@pytest.mark.anyio
async def test_unsigned_archive_refused(sqlite_db_session):
    payload = _make_archive()
    with pytest.raises(UnsignedArchiveError):
        await import_archive(
            sqlite_db_session, archive_payload=payload, signature="", secret=SECRET,
        )


@pytest.mark.anyio
async def test_invalid_signature_refused(sqlite_db_session):
    payload = _make_archive()
    with pytest.raises(UnsignedArchiveError):
        await import_archive(
            sqlite_db_session,
            archive_payload=payload,
            signature="0" * 64,
            secret=SECRET,
        )


@pytest.mark.anyio
async def test_import_inserts_pack_when_db_empty(sqlite_db_session):
    payload = _make_archive("alpha")
    sig = sign_archive(payload, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1
    rows = (await sqlite_db_session.execute(select(TopicPack))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "published"


@pytest.mark.anyio
async def test_import_idempotent_on_content_hash(sqlite_db_session):
    payload = _make_archive("beta")
    sig = sign_archive(payload, secret=SECRET)
    await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    # Second invocation against a now-non-empty DB → skipped entirely.
    out2 = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out2["skipped_db_not_empty"] == 1
    rows = (await sqlite_db_session.execute(select(TopicPack))).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_import_skipped_when_packs_already_present(sqlite_db_session):
    """`AC-PRECOMP-OBJ-2` — destination DB already populated → no-op."""
    # Pre-seed one published pack.
    payload1 = _make_archive("gamma")
    sig1 = sign_archive(payload1, secret=SECRET)
    await import_archive(
        sqlite_db_session, archive_payload=payload1, signature=sig1, secret=SECRET,
    )

    payload2 = _make_archive("delta")
    sig2 = sign_archive(payload2, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload2, signature=sig2, secret=SECRET,
    )
    assert out["skipped_db_not_empty"] == 1
    assert out["packs_inserted"] == 0


# ---------------------------------------------------------------------------
# Read-path wiring — `topic.current_pack_id` must be set so the lookup shim
# (`PrecomputeLookup._published_pack_id`) can return a HIT immediately after
# import. Without this, a freshly imported pack is invisible to /quiz/start.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_sets_topic_current_pack_id(sqlite_db_session):
    from app.models.db import Topic

    payload = _make_archive("epsilon")
    sig = sign_archive(payload, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1

    topic = (
        await sqlite_db_session.execute(select(Topic).where(Topic.slug == "epsilon"))
    ).scalar_one()
    pack = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.topic_id == topic.id))
    ).scalar_one()
    assert topic.current_pack_id == pack.id, (
        "current_pack_id must be set so PrecomputeLookup can resolve the topic"
    )


@pytest.mark.anyio
async def test_import_creates_aliases_when_present(sqlite_db_session):
    """`AC-PRECOMP-LOOKUP-1` — an entry's optional `aliases` array must
    create one canonicalised `topic_aliases` row per alias so the alias-exact
    lookup resolves (e.g. user types "hp house" → "hogwarts-house" topic)."""
    from app.models.db import TopicAlias
    from app.services.precompute.canonicalize import canonical_key_for_name

    doc = {
        "packs": [
            {
                "topic": {"slug": "hogwarts-house", "display_name": "Hogwarts House"},
                "aliases": ["Harry Potter House", "HP House", "Hogwarts"],
                "synopsis": {
                    "content_hash": "syn-" + uuid.uuid4().hex,
                    "body": {"text": "..."},
                },
                "character_set": {
                    "composition_hash": "cs-" + uuid.uuid4().hex,
                    "composition": {"character_ids": []},
                },
                "baseline_question_set": {
                    "composition_hash": "bqs-" + uuid.uuid4().hex,
                    "composition": {"question_ids": []},
                },
                "version": 1,
                "built_in_env": "starter",
            }
        ]
    }
    payload = json.dumps(doc).encode("utf-8")
    sig = sign_archive(payload, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1

    rows = (await sqlite_db_session.execute(select(TopicAlias))).scalars().all()
    keys = {r.alias_normalized for r in rows}
    assert keys == {
        canonical_key_for_name("Harry Potter House"),
        canonical_key_for_name("HP House"),
        canonical_key_for_name("Hogwarts"),
    }, keys


@pytest.mark.anyio
async def test_import_then_lookup_returns_hit(sqlite_db_session):
    """End-to-end: a freshly imported pack must be HIT-eligible via
    `PrecomputeLookup.resolve_topic` (slug-exact path)."""
    from app.services.precompute.lookup import PrecomputeLookup

    payload = _make_archive("zeta")
    sig = sign_archive(payload, secret=SECRET)
    await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )

    lookup = PrecomputeLookup(db=sqlite_db_session, redis=None)
    resolution = await lookup.resolve_topic("Zeta")
    assert resolution is not None, "expected slug-exact HIT after import"
    assert resolution.via == "slug"


# ---------------------------------------------------------------------------
# Phase 3+ — orce_upgrade and inline-character upsert (AC-PRECOMP-IMPORT-1,
# AC-PRECOMP-IMPORT-2).
# ---------------------------------------------------------------------------


def _make_v2_archive_with_characters(topic_slug: str = "v2-topic") -> bytes:
    doc = {
        "packs": [
            {
                "topic": {"slug": topic_slug, "display_name": topic_slug.title()},
                "synopsis": {
                    "content_hash": "syn-" + uuid.uuid4().hex,
                    "body": {"title": "T", "summary": "S"},
                },
                "characters": [
                    {"name": "Alpha", "short_description": "a", "profile_text": "Alpha is alpha."},
                    {"name": "Beta", "short_description": "b", "profile_text": "Beta is beta."},
                ],
                "character_set": {
                    "composition_hash": "cs-" + uuid.uuid4().hex,
                    "composition": {"character_keys": ["alpha", "beta"]},
                },
                "baseline_question_set": {
                    "composition_hash": "bqs-" + uuid.uuid4().hex,
                    "composition": {"question_ids": []},
                },
                "version": 2,
                "built_in_env": "starter",
            }
        ]
    }
    return json.dumps(doc).encode("utf-8")


@pytest.mark.anyio
async def test_force_upgrade_bypasses_db_not_empty_gate(sqlite_db_session):
    """`AC-PRECOMP-IMPORT-1` — `force_upgrade=True` ingests a new
    archive even when the destination DB already has published packs."""
    payload1 = _make_archive("seeded")
    sig1 = sign_archive(payload1, secret=SECRET)
    await import_archive(
        sqlite_db_session, archive_payload=payload1, signature=sig1, secret=SECRET,
    )

    payload2 = _make_v2_archive_with_characters("upgrade-target")
    sig2 = sign_archive(payload2, secret=SECRET)
    out = await import_archive(
        sqlite_db_session,
        archive_payload=payload2,
        signature=sig2,
        secret=SECRET,
        force_upgrade=True,
    )
    assert out["packs_inserted"] == 1
    assert out["skipped_db_not_empty"] == 0


@pytest.mark.anyio
async def test_inline_characters_upserted_and_composition_rewritten(sqlite_db_session):
    """`AC-PRECOMP-IMPORT-2` — inline characters become `Character` rows
    and the persisted `composition` is rewritten to `character_ids`."""
    from app.models.db import Character, CharacterSet

    payload = _make_v2_archive_with_characters("inline-char-topic")
    sig = sign_archive(payload, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1

    chars = (await sqlite_db_session.execute(select(Character))).scalars().all()
    names = {c.name for c in chars}
    assert names == {"Alpha", "Beta"}

    cs = (await sqlite_db_session.execute(select(CharacterSet))).scalar_one()
    assert isinstance(cs.composition, dict)
    ids = cs.composition.get("character_ids")
    assert isinstance(ids, list) and len(ids) == 2
    char_ids = {str(c.id) for c in chars}
    assert set(ids) == char_ids


# ---------------------------------------------------------------------------
# §21 Phase 4 — v3 inline baseline questions (AC-PRECOMP-IMPORT-3).
# ---------------------------------------------------------------------------


def _make_v3_archive_with_questions(topic_slug: str = "v3-topic") -> bytes:
    q_hash_1 = "q-" + uuid.uuid4().hex
    q_hash_2 = "q-" + uuid.uuid4().hex
    doc = {
        "packs": [
            {
                "topic": {"slug": topic_slug, "display_name": topic_slug.title()},
                "synopsis": {
                    "content_hash": "syn-" + uuid.uuid4().hex,
                    "body": {"title": "T", "summary": "S"},
                },
                "characters": [
                    {"name": "Alpha", "short_description": "a", "profile_text": "Alpha is alpha."},
                    {"name": "Beta", "short_description": "b", "profile_text": "Beta is beta."},
                ],
                "character_set": {
                    "composition_hash": "cs-" + uuid.uuid4().hex,
                    "composition": {"character_keys": ["alpha", "beta"]},
                },
                "questions": [
                    {
                        "text_hash": q_hash_1,
                        "text": "What is your favorite color?",
                        "options": [{"text": "Red"}, {"text": "Blue"}],
                        "kind": "baseline",
                    },
                    {
                        "text_hash": q_hash_2,
                        "text": "Pick a season:",
                        "options": [{"text": "Spring"}, {"text": "Fall"}],
                        "kind": "baseline",
                    },
                ],
                "baseline_question_set": {
                    "composition_hash": "bqs-" + uuid.uuid4().hex,
                    "composition": {"question_keys": [q_hash_1, q_hash_2]},
                },
                "version": 3,
                "built_in_env": "starter",
            }
        ]
    }
    return json.dumps(doc).encode("utf-8")


@pytest.mark.anyio
async def test_inline_questions_upserted_and_composition_rewritten(sqlite_db_session):
    """`AC-PRECOMP-IMPORT-3` — inline baseline questions become Question
    rows and the persisted BaselineQuestionSet composition is rewritten to
    ``question_ids``."""
    from app.models.db import BaselineQuestionSet, Question

    payload = _make_v3_archive_with_questions("inline-q-topic")
    sig = sign_archive(payload, secret=SECRET)
    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1

    questions = (await sqlite_db_session.execute(select(Question))).scalars().all()
    assert len(questions) == 2
    assert {q.text for q in questions} == {
        "What is your favorite color?",
        "Pick a season:",
    }
    for q in questions:
        assert q.kind == "baseline"
        assert isinstance(q.options, dict)
        assert isinstance(q.options.get("items"), list)

    bqs = (await sqlite_db_session.execute(select(BaselineQuestionSet))).scalar_one()
    q_ids = bqs.composition.get("question_ids")
    assert isinstance(q_ids, list) and len(q_ids) == 2
    assert set(q_ids) == {str(q.id) for q in questions}


@pytest.mark.anyio
async def test_inline_questions_idempotent_on_text_hash(sqlite_db_session):
    """Re-importing the same v3 archive must not duplicate Question rows."""
    from app.models.db import Question

    payload = _make_v3_archive_with_questions("idempotent-q")
    sig = sign_archive(payload, secret=SECRET)
    await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    n_first = len((await sqlite_db_session.execute(select(Question))).scalars().all())
    await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
        force_upgrade=True,
    )
    n_second = len((await sqlite_db_session.execute(select(Question))).scalars().all())
    assert n_first == n_second == 2
