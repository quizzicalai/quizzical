"""§21 Phase 6 — `POST /admin/precompute/users/forget` zeroes flag linkage
(`AC-PRECOMP-SEC-8`)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.main import API_PREFIX
from app.models.db import ContentFlag
from app.services.precompute.flag_aggregator import hash_ip

API = API_PREFIX.rstrip("/")
URL = f"{API}/admin/precompute/users/forget"


@pytest.fixture()
def operator_token(monkeypatch):
    """Provide a strong OPERATOR_TOKEN so the bearer dep accepts our header."""
    tok = "x" * 48
    monkeypatch.setenv("OPERATOR_TOKEN", tok)
    monkeypatch.setenv("FLAG_HMAC_SECRET", "y" * 48)
    # Force prod-checking off for this test (no 2FA).
    monkeypatch.setattr(settings.app, "environment", "development", raising=False)
    return tok


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_forget_zeroes_flag_linkage(
    async_client, sqlite_db_session, operator_token
):
    user_id = "user-" + uuid.uuid4().hex
    secret = os.getenv("FLAG_HMAC_SECRET")
    user_hash = hash_ip(user_id, secret=secret)

    # Seed two flags whose ip_hash matches the to-be-forgotten user, plus
    # one unrelated row that must NOT be touched.
    sqlite_db_session.add(
        ContentFlag(
            target_kind="topic_pack",
            target_id=str(uuid.uuid4()),
            reason_code="inappropriate",
            reason_text="orig text 1",
            client_ip_hash=user_hash,
        )
    )
    sqlite_db_session.add(
        ContentFlag(
            target_kind="topic_pack",
            target_id=str(uuid.uuid4()),
            reason_code="inappropriate",
            reason_text="orig text 2",
            client_ip_hash=user_hash,
        )
    )
    other = ContentFlag(
        target_kind="topic_pack",
        target_id=str(uuid.uuid4()),
        reason_code="inappropriate",
        reason_text="other",
        client_ip_hash="OTHER" + "0" * 59,
    )
    sqlite_db_session.add(other)
    await sqlite_db_session.commit()

    resp = await async_client.post(
        URL,
        json={"user_id": user_id},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 202
    assert int(resp.json()["scrubbed"]) == 2

    rows = (await sqlite_db_session.execute(select(ContentFlag))).scalars().all()
    scrubbed = [r for r in rows if r.client_ip_hash == "DELETED"]
    untouched = [r for r in rows if r.client_ip_hash != "DELETED"]
    assert len(scrubbed) == 2
    assert all(r.reason_text is None for r in scrubbed)
    assert len(untouched) == 1
    assert untouched[0].reason_text == "other"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_forget_audits_hash_not_raw_user_id_and_does_not_echo_it(
    async_client, sqlite_db_session, operator_token
):
    """Deep-review #25 — a GDPR erasure must NOT re-persist or reflect the raw
    user_id. The append-only audit row must record a ONE-WAY hash as target_id,
    and the response must NOT echo the raw user_id back."""
    from app.models.db import AuditLog

    user_id = "user-" + uuid.uuid4().hex
    secret = os.getenv("FLAG_HMAC_SECRET")
    expected_hash = hash_ip(user_id, secret=secret)

    resp = await async_client.post(
        URL,
        json={"user_id": user_id},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    # The raw user_id is NOT echoed anywhere in the response.
    assert "user_id" not in body
    assert user_id not in resp.text
    assert body["status"] == "accepted"

    # The append-only audit row records the HMAC hash, never the raw user_id.
    audit_rows = (
        await sqlite_db_session.execute(
            select(AuditLog).where(AuditLog.action == "precompute.user_forget")
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.target_kind == "user"
    assert row.target_id == expected_hash
    assert row.target_id != user_id
    assert user_id not in row.target_id
