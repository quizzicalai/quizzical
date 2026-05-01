"""§21 Phase 6 — `POST /content/flag` validation + persistence.

ACs covered:
- `AC-PRECOMP-FLAG-1` — unknown reason_code → 422.
- `AC-PRECOMP-FLAG-2` — long/PII text clamped + scrubbed.
- `AC-PRECOMP-FLAG-3` — raw IP never persisted.
- `AC-PRECOMP-SEC-7` — honeypot codes silently dropped (204, no row).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.main import API_PREFIX
from app.models.db import ContentFlag

API = API_PREFIX.rstrip("/")
URL = f"{API}/content/flag"


def _payload(**kw):
    base = {
        "target_kind": "topic_pack",
        "target_id": str(uuid.uuid4()),
        "reason_code": "inappropriate",
        "reason_text": "test",
    }
    base.update(kw)
    return base


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_unknown_reason_code_returns_422(async_client):
    resp = await async_client.post(URL, json=_payload(reason_code="not_a_real_code"))
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_reason_text_length_capped(async_client, sqlite_db_session):
    long_text = "x" * 5_000  # well over 280 cap
    resp = await async_client.post(URL, json=_payload(reason_text=long_text))
    assert resp.status_code == 202
    rows = (await sqlite_db_session.execute(select(ContentFlag))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason_text is not None
    assert len(rows[0].reason_text) <= 280


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_pii_scrubbed_from_reason_text(async_client, sqlite_db_session):
    payload = _payload(
        reason_text="contact me at foo@bar.com or 555-123-4567 or 192.168.1.1"
    )
    resp = await async_client.post(URL, json=payload)
    assert resp.status_code == 202
    row = (await sqlite_db_session.execute(select(ContentFlag))).scalar_one()
    assert "foo@bar.com" not in (row.reason_text or "")
    assert "192.168.1.1" not in (row.reason_text or "")
    assert "[email]" in (row.reason_text or "") or "[phone]" in (row.reason_text or "")


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_raw_ip_never_persisted(async_client, sqlite_db_session, monkeypatch):
    # Force a known IP via X-Forwarded-For; ensure it is NOT in the row.
    raw_ip = "203.0.113.99"
    resp = await async_client.post(
        URL, json=_payload(), headers={"X-Forwarded-For": raw_ip}
    )
    assert resp.status_code == 202
    row = (await sqlite_db_session.execute(select(ContentFlag))).scalar_one()
    assert raw_ip not in row.client_ip_hash
    assert len(row.client_ip_hash) == 64  # SHA-256 hex


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_honeypot_code_silent_drop(async_client, sqlite_db_session):
    resp = await async_client.post(URL, json=_payload(reason_code="_admin"))
    assert resp.status_code == 204
    rows = (await sqlite_db_session.execute(select(ContentFlag))).scalars().all()
    assert rows == []
