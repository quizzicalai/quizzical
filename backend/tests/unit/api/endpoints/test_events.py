"""Tests for the first-party funnel analytics endpoint (P1 Virality §C).

Covers:
- Valid funnel events (with and without props) -> 204 + structured log line.
- Disallowed event name -> 422 (allow-list enforced).
- Unknown top-level keys / PII smuggling -> 422 (extra=forbid).
- Oversized / non-scalar props -> 422.
- The emitted structured log carries `analytics.event` with event + props only.
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
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "quiz_start", "props": {"big": "x" * 5000}},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_nested_prop_value_rejected_422(async_client):
    resp = await async_client.post(
        "/api/v1/events",
        json={"event": "quiz_start", "props": {"obj": {"nested": 1}}},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_emits_structured_log_line(async_client, caplog):
    with caplog.at_level(logging.INFO):
        resp = await async_client.post(
            "/api/v1/events",
            json={"event": "quiz_complete", "props": {"method": "poll"}},
        )
    assert resp.status_code == 204
    # The structured event name appears in the captured log output.
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "analytics.event" in joined
