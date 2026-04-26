"""
Iteration 4 — Quiz endpoint error paths and integration depth.

Validates how /quiz/start handles failure modes from the agent graph:
- missing synopsis -> 503 with safe message
- agent timeout -> 504 with safe message
- agent crash -> 503 with safe message (already covered in unit tests; kept here
  as integration-style regression too)
- successful run echoes a fresh trace id on every request

Also confirms input boundaries:
- category exactly 3 chars (min) and exactly 100 chars (max) are accepted
- /quiz/proceed with non-existent quiz returns 404
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.main import API_PREFIX, app as fastapi_app
from tests.helpers.sample_payloads import start_quiz_payload


api = API_PREFIX.rstrip("/")


# ---------------------------------------------------------------------------
# /quiz/start: agent failure modes
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_start_503_when_synopsis_missing(client, monkeypatch):
    """If the agent finishes but never produces a synopsis, return 503 cleanly."""
    graph = fastapi_app.state.agent_graph

    async def _aget_state(_config):
        class _Snap:
            values = {
                "synopsis": None,
                "generated_characters": [],
                "messages": [],
                "ideal_archetypes": [],
            }
        return _Snap()

    async def _ainvoke(*_a, **_kw):
        return None

    monkeypatch.setattr(graph, "ainvoke", _ainvoke, raising=False)
    monkeypatch.setattr(graph, "aget_state", _aget_state, raising=False)

    resp = await client.post(f"{api}/quiz/start", json=start_quiz_payload(topic="Cats"))
    assert resp.status_code == 503
    body = resp.json()
    assert "synopsis" in body["detail"].lower()


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_start_504_when_first_step_times_out(client, monkeypatch):
    """If ainvoke times out, the endpoint must surface 504 (gateway timeout)."""
    graph = fastapi_app.state.agent_graph

    async def _slow(*_a, **_kw):
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr(graph, "ainvoke", _slow, raising=False)

    # Force a tiny first-step timeout.
    from app.api.endpoints import quiz as quiz_module

    class _Q:
        first_step_timeout_s = 0.01
        stream_budget_s = 0.01

    class _A:
        environment = "test"

    fake_settings = type("S", (), {})()
    fake_settings.quiz = _Q()
    fake_settings.app = _A()
    monkeypatch.setattr(quiz_module, "settings", fake_settings)

    resp = await client.post(f"{api}/quiz/start", json=start_quiz_payload(topic="Cats"))
    assert resp.status_code == 504
    body = resp.json()
    assert "detail" in body
    # No leak of internal exception text.
    assert "TimeoutError" not in resp.text


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_start_503_when_http_exception_from_dep(client, monkeypatch):
    """If a downstream raises HTTPException, it should propagate, not be wrapped."""
    graph = fastapi_app.state.agent_graph

    async def _ainvoke(*_a, **_kw):
        raise HTTPException(status_code=503, detail="custom upstream")

    monkeypatch.setattr(graph, "ainvoke", _ainvoke, raising=False)

    resp = await client.post(f"{api}/quiz/start", json=start_quiz_payload(topic="Cats"))
    assert resp.status_code == 503
    assert resp.json()["detail"] == "custom upstream"


# ---------------------------------------------------------------------------
# /quiz/start: input boundaries
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
@pytest.mark.parametrize("length", [3, 100])
async def test_start_accepts_category_at_boundary(client, length):
    payload = start_quiz_payload(topic="X" * length)
    resp = await client.post(f"{api}/quiz/start", json=payload)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Trace ID: every successful start gets a fresh trace id
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_consecutive_starts_have_unique_trace_ids(client):
    r1 = await client.post(f"{api}/quiz/start", json=start_quiz_payload(topic="Alpha"))
    r2 = await client.post(f"{api}/quiz/start", json=start_quiz_payload(topic="Bravo"))
    assert r1.status_code == 201 and r2.status_code == 201
    t1 = r1.headers.get("X-Trace-ID")
    t2 = r2.headers.get("X-Trace-ID")
    assert t1 and t2
    assert t1 != t2
    uuid.UUID(t1)
    uuid.UUID(t2)


# ---------------------------------------------------------------------------
# /quiz/proceed: missing session
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_proceed_404_for_unknown_quiz(client):
    """Calling /quiz/proceed with a UUID that was never started must yield 404."""
    qid = str(uuid.uuid4())
    resp = await client.post(f"{api}/quiz/proceed", json={"quiz_id": qid})
    assert resp.status_code in {404, 410}, resp.text
