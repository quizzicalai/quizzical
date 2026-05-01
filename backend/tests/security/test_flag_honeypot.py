"""§21 Phase 6 — flag-endpoint anti-abuse (`AC-PRECOMP-SEC-7`)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.main import API_PREFIX
from app.models.db import ContentFlag

API = API_PREFIX.rstrip("/")
URL = f"{API}/content/flag"


def _payload(target_id: str | None = None, **kw):
    base = {
        "target_kind": "topic_pack",
        "target_id": target_id or str(uuid.uuid4()),
        "reason_code": "inappropriate",
        "reason_text": "x",
    }
    base.update(kw)
    return base


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_honeypot_code_silently_dropped(async_client, sqlite_db_session):
    resp = await async_client.post(
        URL, json=_payload(reason_code="_test_"),
        headers={"X-Forwarded-For": "203.0.113.1"},
    )
    # Honeypot returns 204 (same as abusive shadow-discard) so scanners
    # cannot distinguish accept vs drop.
    assert resp.status_code == 204
    rows = (await sqlite_db_session.execute(select(ContentFlag))).scalars().all()
    assert rows == []


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_abusive_ip_hash_shadow_discarded(async_client, sqlite_db_session):
    """An ip_hash that has flagged > 50 distinct targets in 24 h is
    shadow-discarded — the next submission returns 204 and writes nothing."""
    # Seed 51 prior flags from the same IP across distinct targets.
    from app.services.precompute.flag_aggregator import hash_ip
    from app.core.config import settings

    ip = "203.0.113.7"
    ip_hash = hash_ip(ip, secret=settings.FLAG_HMAC_SECRET)
    for _ in range(51):
        sqlite_db_session.add(
            ContentFlag(
                target_kind="topic_pack",
                target_id=str(uuid.uuid4()),
                reason_code="inappropriate",
                reason_text=None,
                client_ip_hash=ip_hash,
            )
        )
    await sqlite_db_session.commit()

    resp = await async_client.post(
        URL, json=_payload(), headers={"X-Forwarded-For": ip}
    )
    assert resp.status_code == 204
    # Count after — must still be 51.
    n = (await sqlite_db_session.execute(select(ContentFlag))).scalars().all()
    assert len(n) == 51
