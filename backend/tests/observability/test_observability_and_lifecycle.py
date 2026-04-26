"""
Iteration 5 — Observability and end-to-end lifecycle.

Validates:
- Every response carries an X-Trace-ID matching the value used in structured logs.
- Trace ID is preserved across error responses.
- A two-call lifecycle (start -> proceed) succeeds end-to-end against the
  in-memory FakeAgentGraph.
- /api/config returns the expected JSON shape with feature keys.
- /readiness response shape matches contract.
- Logging middleware sets W3C-style trace id headers cleanly.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.main import API_PREFIX
from tests.helpers.sample_payloads import proceed_payload, start_quiz_payload


api = API_PREFIX.rstrip("/")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# Trace id surface
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_trace_id_is_uuid4(client):
    resp = await client.get("/health")
    tid = resp.headers.get("X-Trace-ID")
    assert tid and UUID_RE.match(tid), f"Bad trace id: {tid!r}"
    assert uuid.UUID(tid).version == 4


@pytest.mark.anyio
async def test_404_response_carries_trace_id(client):
    resp = await client.get("/no/such/route/iter5")
    assert resp.status_code == 404
    assert UUID_RE.match(resp.headers.get("X-Trace-ID") or "")


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_validation_failure_carries_trace_id(client):
    resp = await client.post(f"{api}/quiz/start", json={"category": ""})
    assert resp.status_code == 422
    assert UUID_RE.match(resp.headers.get("X-Trace-ID") or "")


@pytest.mark.anyio
async def test_logging_middleware_emits_request_started_and_finished_events(
    client, caplog
):
    """The structured logger should produce request_started/request_finished
    events that include the trace id."""
    import logging

    caplog.set_level(logging.INFO, logger="app.main")
    resp = await client.get("/health")
    tid = resp.headers.get("X-Trace-ID")

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "request_started" in text
    assert "request_finished" in text
    assert tid in text


# ---------------------------------------------------------------------------
# /api/config contract
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_app_config_returns_json_with_features(client):
    resp = await client.get(f"{api}/config")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    # The frontend expects a 'features' (or similar) object; tolerate both shapes.
    assert any(
        k in body for k in ("features", "feature_flags", "config", "frontend")
    ), f"config payload missing recognizable feature container: {list(body)[:10]}"


# ---------------------------------------------------------------------------
# /readiness contract
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_readiness_ready_response_shape(client, monkeypatch):
    from app.api import dependencies as deps

    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)

    resp = await client.get("/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ready"}


# ---------------------------------------------------------------------------
# Two-call lifecycle: start -> proceed
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_lifecycle_start_then_proceed(
    client, fake_redis, fake_cache_store, capture_background_tasks
):
    """Start a quiz, then proceed; both must succeed and produce structured output."""
    # 1. Start
    start_resp = await client.post(
        f"{api}/quiz/start", json=start_quiz_payload(topic="Cats")
    )
    assert start_resp.status_code == 201, start_resp.text
    quiz_id = start_resp.json()["quizId"]
    assert UUID_RE.match(quiz_id)

    # The session row must exist in Redis after /quiz/start
    key = f"quiz_session:{quiz_id}"
    raw = fake_cache_store.get(key)
    # The endpoint's CacheRepository wrote synopsis-bearing state
    assert raw is not None, f"Expected Redis key {key} to be populated"
    state = json.loads(raw)
    assert state.get("synopsis") is not None
    assert state.get("ready_for_questions") is False

    # 2. Proceed
    proceed_resp = await client.post(
        f"{api}/quiz/proceed", json=proceed_payload(quiz_id)
    )
    assert proceed_resp.status_code == 202, proceed_resp.text
    body = proceed_resp.json()
    assert body["status"] == "processing"
    assert body["quizId"] == quiz_id

    # Gate must now be open in Redis
    after = json.loads(fake_cache_store[key])
    assert after.get("ready_for_questions") is True

    # A background task should have been scheduled
    assert len(capture_background_tasks) >= 1
