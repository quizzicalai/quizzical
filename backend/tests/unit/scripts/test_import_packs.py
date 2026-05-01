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
