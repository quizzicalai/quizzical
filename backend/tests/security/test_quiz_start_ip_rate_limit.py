"""
§R16 — per-IP /quiz/start LLM-cost-abuse throttle.

Validates the dependency at app.api.endpoints.quiz._enforce_quiz_start_ip_rate_limit
runs BEFORE verify_turnstile (so blocked IPs never round-trip to Cloudflare or
the LLM agent) and behaves correctly under success / saturation / Redis-failure
conditions.

Acceptance criteria:
- AC-PROD-R16-IPLIMIT-1: Allowed call passes through (no exception).
- AC-PROD-R16-IPLIMIT-2: When the bucket is empty, raises HTTP 429 with
  Retry-After + X-RateLimit-* headers.
- AC-PROD-R16-IPLIMIT-3: Different IPs use independent buckets.
- AC-PROD-R16-IPLIMIT-4: Redis errors fail-open (do not raise) so an outage
  cannot DOS legitimate users.
- AC-PROD-R16-IPLIMIT-5: When the per-IP RL config is disabled, dependency
  is a no-op.
- AC-PROD-R16-IPLIMIT-6: Dependency keys the bucket by client IP, honouring
  the X-Forwarded-For first hop (Container Apps / Kong inject it).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.endpoints import quiz as quiz_module


def _fake_request(*, ip: str = "203.0.113.10", xff: str | None = None) -> SimpleNamespace:
    """Minimal stand-in for fastapi.Request the dependency consumes."""
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    # Mimic dict.get(key) behaviour our _client_ip helper uses.
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=ip),
        app=SimpleNamespace(dependency_overrides={}),
    )


def _redis_returning(allowed: int, remaining: int = 0, retry_after: int = 7):
    """Build a fake Redis whose eval() returns a Lua-script-shaped triple."""
    redis = SimpleNamespace()
    redis.eval = AsyncMock(return_value=[allowed, remaining, retry_after])
    return redis


@pytest.fixture(autouse=True)
def _inject_fake_redis(monkeypatch):
    """Patch the Redis client factory the dependency imports lazily."""
    holder: dict = {}

    def _factory():
        return holder.get("redis")

    monkeypatch.setattr(
        "app.api.dependencies.get_redis_client", _factory, raising=True
    )
    yield holder


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allowed_call_does_not_raise(_inject_fake_redis, monkeypatch):
    _inject_fake_redis["redis"] = _redis_returning(allowed=1, remaining=2, retry_after=0)
    # Ensure the throttle is enabled with a sane default.
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", True, raising=False
    )

    # Should not raise.
    await quiz_module._enforce_quiz_start_ip_rate_limit(_fake_request())


# ---------------------------------------------------------------------------
# Saturation → 429 with headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saturated_bucket_raises_429_with_headers(_inject_fake_redis, monkeypatch):
    _inject_fake_redis["redis"] = _redis_returning(allowed=0, remaining=0, retry_after=11)
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", True, raising=False
    )
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "capacity", 3, raising=False
    )

    with pytest.raises(HTTPException) as exc:
        await quiz_module._enforce_quiz_start_ip_rate_limit(_fake_request())

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "11"
    assert exc.value.headers["X-RateLimit-Limit"] == "3"
    assert exc.value.headers["X-RateLimit-Remaining"] == "0"


# ---------------------------------------------------------------------------
# Per-IP isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_ips_use_independent_buckets(_inject_fake_redis, monkeypatch):
    redis = _redis_returning(allowed=1, remaining=2, retry_after=0)
    _inject_fake_redis["redis"] = redis
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", True, raising=False
    )

    await quiz_module._enforce_quiz_start_ip_rate_limit(
        _fake_request(ip="203.0.113.10")
    )
    await quiz_module._enforce_quiz_start_ip_rate_limit(
        _fake_request(ip="198.51.100.20")
    )

    keys_used = [call.args[2] for call in redis.eval.await_args_list]
    assert "rl:quiz_start:203.0.113.10" in keys_used
    assert "rl:quiz_start:198.51.100.20" in keys_used


# ---------------------------------------------------------------------------
# Redis outage → fail-open (do not DOS legit users)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_outage_fails_open(_inject_fake_redis, monkeypatch):
    redis = SimpleNamespace()
    redis.eval = AsyncMock(side_effect=RuntimeError("redis down"))
    _inject_fake_redis["redis"] = redis
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", True, raising=False
    )

    # Must NOT raise — RateLimiter.check fails open and returns allowed=True
    # via its own try/except, so the dependency falls through silently.
    await quiz_module._enforce_quiz_start_ip_rate_limit(_fake_request())


# ---------------------------------------------------------------------------
# Disabled config → no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_config_is_noop(_inject_fake_redis, monkeypatch):
    redis = _redis_returning(allowed=0, remaining=0, retry_after=99)
    _inject_fake_redis["redis"] = redis
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", False, raising=False
    )

    # Even though the bucket would block, the disabled flag short-circuits.
    await quiz_module._enforce_quiz_start_ip_rate_limit(_fake_request())
    redis.eval.assert_not_awaited()


# ---------------------------------------------------------------------------
# X-Forwarded-For first hop is honoured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_xff_first_hop_is_used(_inject_fake_redis, monkeypatch):
    redis = _redis_returning(allowed=1, remaining=1, retry_after=0)
    _inject_fake_redis["redis"] = redis
    monkeypatch.setattr(
        quiz_module.settings.security.quiz_start_rate_limit, "enabled", True, raising=False
    )

    req = _fake_request(ip="10.0.0.1", xff="198.51.100.77, 10.0.0.1")
    await quiz_module._enforce_quiz_start_ip_rate_limit(req)

    keys_used = [call.args[2] for call in redis.eval.await_args_list]
    assert keys_used == ["rl:quiz_start:198.51.100.77"]
