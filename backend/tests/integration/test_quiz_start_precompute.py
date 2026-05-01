"""§21 Phase 2 — `/quiz/start` precompute lookup shim.

The lookup is read-only and gated by `settings.precompute.enabled`. Until
Phase 3 introduces the build-from-pack response helper, even an `enabled=True`
HIT must fall through to the live agent so the response stays byte-for-byte
identical (Universal-G5). These tests pin both contracts:

- **OFF (default)**  → no lookup is invoked; existing behaviour is preserved.
- **ON, MISS**       → lookup runs, returns None, agent path proceeds; the
                        miss is observable via the `precompute.lookup.miss`
                        structlog event.

A future HIT-side test lands in Phase 3 once the response builder exists.
"""

from __future__ import annotations

import uuid

import pytest
import structlog
from sqlalchemy import select

from app.core.config import settings
from app.main import API_PREFIX
from app.models.db import SessionHistory
from tests.helpers.sample_payloads import start_quiz_payload

API = API_PREFIX.rstrip("/")


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-1 — flag OFF means the shim is inert.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_with_precompute_disabled_is_unchanged(
    client,
    sqlite_db_session,
    monkeypatch,
):
    """Default config (`enabled=False`) leaves `/quiz/start` byte-for-byte
    unchanged — no lookup is invoked, no precompute telemetry is emitted,
    and the response shape matches the pre-§21 happy path."""

    # Force the flag OFF for this test irrespective of the YAML default.
    monkeypatch.setattr(settings.precompute, "enabled", False)

    payload = start_quiz_payload(topic="Cats")
    with structlog.testing.capture_logs() as captured:
        resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    quiz_id = body["quizId"]
    assert uuid.UUID(quiz_id).version == 4
    assert body["initialPayload"]["type"] == "synopsis"

    events = {entry.get("event") for entry in captured}
    assert "precompute.start.lookup" not in events, captured
    assert "precompute.lookup.hit" not in events, captured
    assert "precompute.lookup.miss" not in events, captured

    row = (
        await sqlite_db_session.execute(
            select(SessionHistory).where(SessionHistory.session_id == uuid.UUID(quiz_id))
        )
    ).scalar_one_or_none()
    assert row is not None


# ---------------------------------------------------------------------------
# AC-PRECOMP-LOOKUP-2 — flag ON, no published pack → MISS, agent fallback.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_with_precompute_enabled_miss_falls_through(
    client,
    sqlite_db_session,
    monkeypatch,
):
    """With `enabled=True` but an empty topics table, `resolve_topic` returns
    None, the `precompute.start.lookup` telemetry fires with `hit=False`,
    and the live agent still produces the response."""

    monkeypatch.setattr(settings.precompute, "enabled", True)

    payload = start_quiz_payload(topic="A Brand New Universe")
    with structlog.testing.capture_logs() as captured:
        resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["initialPayload"]["type"] == "synopsis"

    lookup_logs = [e for e in captured if e.get("event") == "precompute.start.lookup"]
    assert lookup_logs, captured
    assert lookup_logs[0].get("hit") is False

    row = (
        await sqlite_db_session.execute(
            select(SessionHistory).where(SessionHistory.session_id == uuid.UUID(body["quizId"]))
        )
    ).scalar_one_or_none()
    assert row is not None
