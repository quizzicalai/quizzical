"""§21 Phase 3 — admin endpoint auth + audit tests.

Covers:
  - AC-PRECOMP-SEC-3 (bearer required; missing → 401)
  - AC-PRECOMP-SEC-4 (invalid bearer → 401)
  - AC-PRECOMP-SEC-8 (production env requires `X-Operator-2FA` → 403 if absent)
  - AC-PRECOMP-FLAG-6 (every mutation writes an `audit_log` row)
  - AC-PRECOMP-PROMOTE-1 (promote sets `topics.current_pack_id` atomically)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.db import (
    AuditLog,
    BaselineQuestionSet,
    CharacterSet,
    Synopsis,
    Topic,
    TopicPack,
)

pytestmark = pytest.mark.anyio


_TOKEN = "z" * 64  # fits the 32-byte minimum and is easy to inspect.


@pytest.fixture
def operator_token(monkeypatch):
    """Set OPERATOR_TOKEN via env so `settings.OPERATOR_TOKEN` returns it."""
    monkeypatch.setenv("OPERATOR_TOKEN", _TOKEN)
    yield _TOKEN
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)


async def _seed_topic_with_published_pack(session) -> tuple[Topic, TopicPack]:
    topic = Topic(slug="t", display_name="T", policy_status="allowed")
    session.add(topic)
    await session.flush()

    syn = Synopsis(topic_id=topic.id, content_hash="h-syn", body={"x": 1})
    cs = CharacterSet(composition_hash="h-cs", composition={"members": []})
    bqs = BaselineQuestionSet(composition_hash="h-bqs", composition={"questions": []})
    session.add_all([syn, cs, bqs])
    await session.flush()

    pack = TopicPack(
        topic_id=topic.id, version=1, status="published",
        synopsis_id=syn.id, character_set_id=cs.id, baseline_question_set_id=bqs.id,
        model_provenance={"prompts": []}, built_in_env="test",
    )
    session.add(pack)
    await session.commit()
    return topic, pack


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_enqueue_without_bearer_returns_401(client, operator_token, sqlite_db_session):
    topic, _ = await _seed_topic_with_published_pack(sqlite_db_session)
    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/jobs",
        json={"topic_id": str(topic.id)},
    )
    assert r.status_code == 401


async def test_enqueue_with_bad_bearer_returns_401(client, operator_token, sqlite_db_session):
    topic, _ = await _seed_topic_with_published_pack(sqlite_db_session)
    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/jobs",
        json={"topic_id": str(topic.id)},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


async def test_enqueue_in_prod_without_2fa_returns_403(
    client, operator_token, sqlite_db_session, monkeypatch,
):
    monkeypatch.setattr(settings.app, "environment", "production")
    topic, _ = await _seed_topic_with_published_pack(sqlite_db_session)
    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/jobs",
        json={"topic_id": str(topic.id)},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Happy-path mutations write audit rows
# ---------------------------------------------------------------------------


async def test_enqueue_creates_job_and_audit_row(client, operator_token, sqlite_db_session):
    topic, _ = await _seed_topic_with_published_pack(sqlite_db_session)
    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/jobs",
        json={"topic_id": str(topic.id)},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["topic_id"] == str(topic.id)

    # audit row recorded.
    rows = (await sqlite_db_session.execute(
        select(AuditLog).where(AuditLog.action == "precompute.enqueue")
    )).scalars().all()
    assert any(a.target_id == str(topic.id) for a in rows)


async def test_promote_sets_current_pack_and_audits(client, operator_token, sqlite_db_session):
    topic, pack = await _seed_topic_with_published_pack(sqlite_db_session)
    # Initially the seed sets current_pack_id=None; promote should set it.
    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/promote",
        json={"topic_id": str(topic.id), "pack_id": str(pack.id)},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 200, r.text

    await sqlite_db_session.refresh(topic)
    assert topic.current_pack_id == pack.id

    audits = (await sqlite_db_session.execute(
        select(AuditLog).where(AuditLog.action == "precompute.promote")
    )).scalars().all()
    assert any(a.target_id == str(topic.id) for a in audits)


async def test_enqueue_banned_topic_returns_409(client, operator_token, sqlite_db_session):
    t = Topic(slug="b", display_name="B", policy_status="banned")
    sqlite_db_session.add(t)
    await sqlite_db_session.commit()

    r = await client.post(
        f"{settings.project.api_prefix}/admin/precompute/jobs",
        json={"topic_id": str(t.id)},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "TOPIC_BANNED"


async def test_cost_view_returns_snapshot_shape(client, operator_token):
    r = await client.get(
        f"{settings.project.api_prefix}/admin/precompute/cost",
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("spent_cents", "daily_cap_cents", "tier3_cap_cents", "remaining_cents"):
        assert k in body
        assert isinstance(body[k], int)
