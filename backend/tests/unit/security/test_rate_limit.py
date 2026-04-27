# tests/unit/security/test_rate_limit.py
"""§15.1 — Redis token-bucket rate limiter (AC-RL-1..7)."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture
def limiter_module():
    from app.security import rate_limit as rl
    return rl


@pytest.fixture
def fake_redis():
    """Minimal fake exposing only what RateLimiter touches."""
    class _R:
        def __init__(self):
            self.calls = []

        async def evalsha(self, *a, **k):
            raise AssertionError("unexpected evalsha")

        async def eval(self, script, numkeys, *args):
            self.calls.append(("eval", script[:30], numkeys, args))
            return [int(self._allowed), int(self._remaining), int(self._retry_after)]

        # Default: allow with plenty of remaining tokens.
        _allowed = 1
        _remaining = 25
        _retry_after = 0

    return _R()


# AC-RL-1: tokens available -> allowed
@pytest.mark.asyncio
async def test_check_allows_when_tokens_available(limiter_module, fake_redis):
    limiter = limiter_module.RateLimiter(redis=fake_redis, capacity=30, refill_per_second=1.0)
    res = await limiter.check("ip:1.2.3.4|/api/quiz/start")
    assert res.allowed is True
    assert res.remaining >= 0
    assert res.retry_after_s == 0


# AC-RL-2: empty bucket -> denied with retry_after
@pytest.mark.asyncio
async def test_check_denies_when_empty(limiter_module, fake_redis):
    fake_redis._allowed = 0
    fake_redis._remaining = 0
    fake_redis._retry_after = 5
    limiter = limiter_module.RateLimiter(redis=fake_redis, capacity=30, refill_per_second=1.0)
    res = await limiter.check("ip:1.2.3.4|/api/quiz/start")
    assert res.allowed is False
    assert res.remaining == 0
    assert res.retry_after_s == 5


# AC-RL-4: redis error -> fail open
@pytest.mark.asyncio
async def test_check_fails_open_on_redis_error(limiter_module):
    class _Boom:
        async def eval(self, *a, **k):
            raise RuntimeError("redis down")
    limiter = limiter_module.RateLimiter(redis=_Boom(), capacity=30, refill_per_second=1.0)
    res = await limiter.check("ip:x|/api/x")
    assert res.allowed is True
    assert res.fail_open is True


# AC-RL-5: bucket key includes IP and route prefix
def test_bucket_key_format(limiter_module):
    key = limiter_module.bucket_key(client_ip="9.9.9.9", path="/api/quiz/start")
    assert "9.9.9.9" in key
    assert "/api/quiz" in key  # coarse route prefix


# AC-RL-3: middleware skips allowlisted paths
@pytest.mark.asyncio
async def test_middleware_skips_allowlisted_paths(limiter_module):
    seen = []

    async def call_next(req):
        seen.append(req.url.path)
        class _R:
            headers: dict[str, str] = {}
        return _R()

    class _URL:
        def __init__(self, p): self.path = p
    class _Client:
        host = "1.1.1.1"
    class _Headers(dict):
        def get(self, k, d=None): return super().get(k.lower(), d)
    class _Req:
        def __init__(self, p):
            self.url = _URL(p); self.client = _Client(); self.headers = _Headers()

    fake_redis = AsyncMock()
    mw = limiter_module.RateLimitMiddleware(
        app=None, redis_factory=lambda: fake_redis,
        capacity=30, refill_per_second=1.0,
        allow_paths=["/health", "/readiness", "/docs"],
    )
    for path in ["/health", "/readiness", "/docs"]:
        await mw.dispatch(_Req(path), call_next)
    assert seen == ["/health", "/readiness", "/docs"]
    fake_redis.eval.assert_not_called()


# AC-RL-7: disabled middleware is a no-op
@pytest.mark.asyncio
async def test_middleware_disabled_is_noop(limiter_module):
    seen = []

    async def call_next(req):
        seen.append(req.url.path)
        class _R:
            headers: dict[str, str] = {}
        return _R()

    class _URL:
        def __init__(self, p): self.path = p
    class _Client:
        host = "1.1.1.1"
    class _Headers(dict):
        def get(self, k, d=None): return super().get(k.lower(), d)
    class _Req:
        def __init__(self, p):
            self.url = _URL(p); self.client = _Client(); self.headers = _Headers()

    fake_redis = AsyncMock()
    mw = limiter_module.RateLimitMiddleware(
        app=None, redis_factory=lambda: fake_redis,
        capacity=30, refill_per_second=1.0,
        enabled=False, allow_paths=[],
    )
    await mw.dispatch(_Req("/api/quiz/start"), call_next)
    assert seen == ["/api/quiz/start"]
    fake_redis.eval.assert_not_called()
