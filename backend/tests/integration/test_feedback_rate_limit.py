"""§9.7.4 — Per-quiz feedback rate limit (AC-FEEDBACK-RL-1..4).

A single quiz_id can be rated only `capacity` times in fast succession before
the throttle kicks in. The throttle is per-quiz (not per-IP) so a user who
rates many different quizzes is never blocked.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.endpoints import feedback as feedback_mod
from app.api.dependencies import get_redis_client
from app.core.config import settings

# Reuse existing fixtures
from tests.fixtures.turnstile_fixtures import turnstile_bypass  # noqa: F401
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401


class _ScriptedRedis:
    """Fake redis whose `eval` response is controlled per-call.

    Mimics the contract `RateLimiter` expects: returns
    [allowed, remaining, retry_after_s].
    """

    def __init__(self) -> None:
        self.queue: list[tuple[int, int, int]] = []
        self.eval_calls: list[tuple[Any, ...]] = []

    def queue_response(self, allowed: int, remaining: int, retry_after: int) -> None:
        self.queue.append((allowed, remaining, retry_after))

    async def eval(self, script, numkeys, *args):  # noqa: D401
        self.eval_calls.append(args)
        if not self.queue:
            # Default: allow with plenty of capacity.
            return [1, 99, 0]
        allowed, remaining, retry_after = self.queue.pop(0)
        return [allowed, remaining, retry_after]


@pytest.fixture
def scripted_redis():
    return _ScriptedRedis()


@pytest.fixture
def override_redis(scripted_redis, monkeypatch):
    """Patch the module-level `get_redis_client` so the endpoint uses our fake."""
    monkeypatch.setattr(feedback_mod, "get_redis_client", lambda: scripted_redis)
    return scripted_redis


@pytest.fixture
def mock_session_repo(monkeypatch):
    inst = MagicMock()
    inst.save_feedback = AsyncMock(return_value={"id": uuid.uuid4()})
    monkeypatch.setattr(feedback_mod, "SessionRepository", MagicMock(return_value=inst))
    return inst


@pytest.fixture
def enable_feedback_rl():
    """Ensure the feedback rate limit is enabled with predictable settings."""
    cfg = settings.security.feedback_rate_limit
    original = (cfg.enabled, cfg.capacity, cfg.refill_per_second)
    cfg.enabled = True
    cfg.capacity = 3
    cfg.refill_per_second = 1.0 / 60.0
    try:
        yield cfg
    finally:
        cfg.enabled, cfg.capacity, cfg.refill_per_second = original


@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency", "enable_feedback_rl")
async def test_first_submission_allowed(async_client, override_redis, mock_session_repo):
    """AC-FEEDBACK-RL-1: first submission for a quiz returns 204."""
    payload = {"quiz_id": str(uuid.uuid4()), "rating": "up"}
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 204
    mock_session_repo.save_feedback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency", "enable_feedback_rl")
async def test_throttle_returns_429_with_retry_after(async_client, override_redis, mock_session_repo):
    """AC-FEEDBACK-RL-2: when bucket is empty, return 429 with Retry-After header."""
    override_redis.queue_response(allowed=0, remaining=0, retry_after=42)
    payload = {"quiz_id": str(uuid.uuid4()), "rating": "down"}
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "42"
    body = response.json()
    assert "too many" in body["detail"].lower()
    # When throttled the DB write must NOT happen.
    mock_session_repo.save_feedback.assert_not_called()


@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency", "enable_feedback_rl")
async def test_bucket_key_uses_quiz_id(async_client, override_redis, mock_session_repo):
    """AC-FEEDBACK-RL-3: bucket key is per-quiz_id (not per-IP)."""
    qid_a = uuid.uuid4()
    qid_b = uuid.uuid4()
    await async_client.post("/api/v1/feedback", json={"quiz_id": str(qid_a), "rating": "up"})
    await async_client.post("/api/v1/feedback", json={"quiz_id": str(qid_b), "rating": "up"})
    # Each call's eval received the bucket key as KEYS[1] via `numkeys=1` then
    # capacity/refill/now in ARGV. The key is passed positionally before ARGV;
    # the limiter calls `eval(script, 1, key, capacity, refill, now)`.
    # Our scripted_redis stores everything after numkeys in eval_calls.
    keys_seen = [call[0] for call in override_redis.eval_calls]
    assert any(str(qid_a) in k for k in keys_seen), keys_seen
    assert any(str(qid_b) in k for k in keys_seen), keys_seen


@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency", "enable_feedback_rl")
async def test_redis_failure_fails_open(async_client, mock_session_repo, monkeypatch):
    """AC-FEEDBACK-RL-4: Redis errors must NOT block legitimate feedback."""

    class _Boom:
        async def eval(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(feedback_mod, "get_redis_client", lambda: _Boom())
    payload = {"quiz_id": str(uuid.uuid4()), "rating": "up"}
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 204
