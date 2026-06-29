"""Tests for the first-party funnel analytics endpoint (P1 Virality §C).

Covers:
- Valid funnel events (with and without props) -> 204 + structured log line.
- Disallowed event name -> 422 (allow-list enforced).
- Unknown top-level keys / PII smuggling -> 422 (extra=forbid).
- Oversized / non-scalar props -> 422.
- The emitted structured log carries the `analytics.event` message with the
  funnel name under `event_name` (NOT `event`, which is reserved by structlog).
"""
import logging

import pytest

from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_valid_event_no_props_204(async_client):
    resp = await async_client.post("/api/v1/events", json={"event": "quiz_start"})
    assert resp.status_code == 204
    assert resp.content in (b"", None) or resp.text == ""


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_valid_event_with_props_204(async_client):
    # `method` is allow-listed; `n`/`ok` are NOT and are silently dropped
    # (PII hygiene) — the request still succeeds.
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "share_click", "props": {"method": "x", "n": 1, "ok": True}},
    )
    assert resp.status_code == 204


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_all_three_funnel_events_accepted(async_client):
    for ev in ("quiz_start", "quiz_complete", "share_click"):
        resp = await async_client.post("/api/v1/events", json={"event": ev})
        assert resp.status_code == 204, ev


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_disallowed_event_name_422(async_client):
    resp = await async_client.post("/api/v1/events", json={"event": "login"})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_extra_top_level_key_rejected_422(async_client):
    # `extra="forbid"` blocks attempts to smuggle PII alongside the event.
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "quiz_start", "email": "user@example.com"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_too_many_props_422(async_client):
    props = {f"k{i}": i for i in range(20)}
    resp = await async_client.post(
        "/api/v1/events", json={"event": "quiz_start", "props": props}
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_oversized_prop_value_422(async_client):
    # Uses an allow-listed key so the value-length guard is what fires.
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "quiz_start", "props": {"method": "x" * 5000}},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_nested_prop_value_rejected_422(async_client):
    # Uses an allow-listed key so the scalar-only guard is what fires.
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "quiz_start", "props": {"method": {"nested": 1}}},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_non_allowlisted_prop_key_dropped_not_logged(async_client, caplog):
    # PII hygiene: a non-allow-listed key (e.g. an email) is silently dropped,
    # never 422'd and never logged. The request still succeeds (204).
    import logging

    with caplog.at_level(logging.INFO):
        resp = await async_client.post(
            "/api/v1/events",
            json={
                "event": "share_click",
                "props": {"email": "user@example.com", "method": "copy"},
            },
        )
    assert resp.status_code == 204
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "user@example.com" not in joined
    # The allow-listed prop is retained.
    assert "copy" in joined


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_emits_structured_log_line(async_client, caplog):
    with caplog.at_level(logging.INFO):
        resp = await async_client.post(
            "/api/v1/events",
            json={"event": "quiz_complete", "props": {"method": "poll"}},
        )
    # A 204 here (rather than a 500) is itself the regression guard: passing
    # `event=` to structlog's BoundLogger.info() raised TypeError inside the
    # handler. We log the funnel name under `event_name` instead.
    assert resp.status_code == 204
    # The structured event message and the funnel name appear in the output.
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "analytics.event" in joined
    assert "quiz_complete" in joined
