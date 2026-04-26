"""
Iteration 1 — Security: input validation and request hardening.

Validates that the public API rejects malformed, oversized, and unsafe inputs
with the right HTTP status, and that successful requests don't leak server
internals (stack traces, secrets, env vars, file paths) in the response body.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.main import API_PREFIX
from tests.helpers.sample_payloads import start_quiz_payload


SECRET_LIKE_RE = re.compile(
    r"(api[_-]?key|secret|password|token|sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# /quiz/start input validation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_rejects_short_category(client):
    api = API_PREFIX.rstrip("/")
    payload = start_quiz_payload(topic="ab")  # below min_length=3
    resp = await client.post(f"{api}/quiz/start", json=payload)
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_rejects_oversized_category(client):
    api = API_PREFIX.rstrip("/")
    payload = start_quiz_payload(topic="X" * 5000)
    resp = await client.post(f"{api}/quiz/start", json=payload)
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_rejects_missing_required_fields(client):
    api = API_PREFIX.rstrip("/")
    resp = await client.post(f"{api}/quiz/start", json={})
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI puts validation details under "detail"
    assert "detail" in body


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_rejects_invalid_json(client):
    api = API_PREFIX.rstrip("/")
    resp = await client.post(
        f"{api}/quiz/start",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_handles_unicode_safely(client):
    """Unicode (incl. emoji and non-Latin) should be accepted as a valid category."""
    api = API_PREFIX.rstrip("/")
    payload = start_quiz_payload(topic="日本語クイズ 🎉")
    resp = await client.post(f"{api}/quiz/start", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert "quizId" in body
    uuid.UUID(body["quizId"])


# ---------------------------------------------------------------------------
# /quiz/proceed and /quiz/next input validation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_proceed_rejects_invalid_uuid(client):
    api = API_PREFIX.rstrip("/")
    resp = await client.post(f"{api}/quiz/proceed", json={"quiz_id": "not-a-uuid"})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_next_rejects_negative_index(client):
    api = API_PREFIX.rstrip("/")
    qid = str(uuid.uuid4())
    resp = await client.post(
        f"{api}/quiz/next",
        json={"quiz_id": qid, "question_index": -1, "option_index": 0},
    )
    # Either rejected by validation (422) or by business rule (400/404)
    assert resp.status_code in {400, 404, 422}


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_next_rejects_huge_index(client):
    api = API_PREFIX.rstrip("/")
    qid = str(uuid.uuid4())
    resp = await client.post(
        f"{api}/quiz/next",
        json={"quiz_id": qid, "question_index": 10**9, "option_index": 0},
    )
    assert resp.status_code in {400, 404, 422}


# ---------------------------------------------------------------------------
# Response sanitization
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_validation_error_does_not_leak_secrets(client):
    api = E = API_PREFIX.rstrip("/")  # noqa: F841 - readability
    resp = await client.post(f"{api}/quiz/start", json={"category": "x"})
    text = resp.text
    # Validation error responses must not contain anything secret-shaped.
    assert not SECRET_LIKE_RE.search(text), (
        f"Validation response unexpectedly contains secret-like value: {text[:200]}"
    )


@pytest.mark.anyio
async def test_health_response_has_no_internal_paths(client):
    resp = await client.get("/health")
    body = resp.text
    # Health response should never echo OS path separators
    assert "C:\\" not in body and "/home/" not in body
    assert "Traceback" not in body


# ---------------------------------------------------------------------------
# HTTP method correctness
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_quiz_start_get_not_allowed(client):
    api = API_PREFIX.rstrip("/")
    resp = await client.get(f"{api}/quiz/start")
    assert resp.status_code == 405


@pytest.mark.anyio
async def test_health_post_not_allowed(client):
    resp = await client.post("/health", json={})
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Trace ID guarantees (defense-in-depth)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_every_response_carries_unique_trace_id(client):
    r1 = await client.get("/health")
    r2 = await client.get("/health")
    t1 = r1.headers.get("X-Trace-ID")
    t2 = r2.headers.get("X-Trace-ID")
    assert t1 and t2
    assert t1 != t2
    uuid.UUID(t1)
    uuid.UUID(t2)


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_validation_failures_still_emit_trace_id(client):
    api = API_PREFIX.rstrip("/")
    resp = await client.post(f"{api}/quiz/start", json={})
    assert resp.status_code == 422
    assert resp.headers.get("X-Trace-ID")


# ---------------------------------------------------------------------------
# Content-Type negotiation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_unknown_route_returns_404_json(client):
    resp = await client.get("/this/route/does/not/exist")
    assert resp.status_code == 404
    # FastAPI's default 404 returns JSON
    assert "application/json" in (resp.headers.get("content-type") or "").lower()
    # Should be parseable
    json.loads(resp.text)
