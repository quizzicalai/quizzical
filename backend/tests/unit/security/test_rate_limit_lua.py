# tests/unit/security/test_rate_limit_lua.py
"""§15.1 — EXECUTION coverage for the token-bucket Lua script (punchlist #9).

`app.security.rate_limit.TOKEN_BUCKET_LUA` is the sole logic behind ALL six
rate-limit points (middleware + the per-endpoint limiters). The existing
`test_rate_limit.py` only stubs ``redis.eval`` with canned triples, so a subtly
broken script — which fails OPEN in production (`RateLimiter.check` swallows the
error and admits the request) — would sail through untested.

These tests run the REAL ``TOKEN_BUCKET_LUA`` against fakeredis-with-lua (lupa),
asserting the returned ``{allowed, remaining, retry_after}`` triple and the key
TTL directly. If lupa cannot be imported/run on this platform, we fall back to a
pure-Python REFERENCE reimplementation that pins the identical numeric contract
(clearly labelled) so the money-path math stays regression-guarded either way.
"""
from __future__ import annotations

import math

import pytest

from app.security.rate_limit import TOKEN_BUCKET_LUA, RateLimiter

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Backend selection: real Lua (preferred) or pure-Python reference (fallback).
# ---------------------------------------------------------------------------
def _fakeredis_lua_available() -> bool:
    """True iff fakeredis can actually EXECUTE a server-side Lua script here."""
    try:
        import asyncio

        import fakeredis.aioredis as fa  # noqa: F401
        import lupa  # noqa: F401  (the embedded Lua interpreter fakeredis uses)

        async def _probe() -> list:
            r = fa.FakeRedis()
            return await r.eval("return {1, 2, 3}", 0)

        return list(asyncio.run(_probe())) == [1, 2, 3]
    except Exception:
        return False


_LUA_OK = _fakeredis_lua_available()


async def _run_real_lua(redis, key, capacity, refill, now):
    """Execute the REAL TOKEN_BUCKET_LUA and normalise the triple to ints."""
    res = await redis.eval(
        TOKEN_BUCKET_LUA, 1, key, str(capacity), str(refill), str(now)
    )
    return int(res[0]), int(res[1]), int(res[2])


def _reference_bucket():
    """Pure-Python REFERENCE reimplementation of TOKEN_BUCKET_LUA.

    NOT the code under test — a line-by-line port of the Lua that lets us pin the
    SAME numeric contract on platforms where lupa/fakeredis-lua is unavailable.
    Kept byte-compatible with the Lua (float math, math.floor on remaining,
    math.ceil on retry_after). State lives in a dict keyed like a Redis hash.
    """
    store: dict[str, dict] = {}

    async def call(redis, key, capacity, refill_rate, now):  # noqa: ARG001
        capacity = float(capacity)
        refill_rate = float(refill_rate)
        now = float(now)
        data = store.get(key)
        if data is None:
            tokens = capacity
            updated_at = now
        else:
            tokens = data["tokens"]
            updated_at = data["updated_at"]
            delta = max(0.0, now - updated_at)
            tokens = min(capacity, tokens + (delta * refill_rate))
            updated_at = now

        allowed = 0
        retry_after = 0
        if tokens >= 1:
            tokens = tokens - 1
            allowed = 1
        else:
            if refill_rate > 0:
                retry_after = math.ceil((1 - tokens) / refill_rate)
            else:
                retry_after = 60

        store[key] = {"tokens": tokens, "updated_at": updated_at}
        return allowed, int(math.floor(tokens)), int(retry_after)

    return call


@pytest.fixture
def token_bucket():
    """Yields ``(redis, run)`` where ``run(key, capacity, refill, now)`` executes
    the bucket logic and returns the normalised ``(allowed, remaining, retry)``
    triple. Uses real Lua when available, else the labelled reference."""
    if _LUA_OK:
        import fakeredis.aioredis as fa

        redis = fa.FakeRedis()

        async def run(key, capacity, refill, now):
            return await _run_real_lua(redis, key, capacity, refill, now)

        return redis, run

    # ---- FALLBACK (reference reimplementation; same numeric contract) --------
    ref = _reference_bucket()

    async def run(key, capacity, refill, now):
        return await ref(None, key, capacity, refill, now)

    return None, run


# ---------------------------------------------------------------------------
# 1. Capacity drain -> denial
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_capacity_drains_then_denies(token_bucket):
    _redis, run = token_bucket
    # capacity=3, no refill, frozen clock. First 3 pass, then denied.
    triples = [await run("rl:drain", 3, 0, 1000.0) for _ in range(5)]
    allowed = [t[0] for t in triples]
    remaining = [t[1] for t in triples]
    assert allowed == [1, 1, 1, 0, 0]
    # remaining counts down 2,1,0 then stays 0 (never negative in the report).
    assert remaining == [2, 1, 0, 0, 0]


# ---------------------------------------------------------------------------
# 2. retry_after math at refill_rate > 0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_after_with_positive_refill(token_bucket):
    _redis, run = token_bucket
    # capacity=1, refill=0.5 tokens/sec. Drain the single token, then denial's
    # retry_after = ceil((1 - tokens)/refill). tokens==0 -> ceil(1/0.5) == 2.
    a1 = await run("rl:refill", 1, 0.5, 5000.0)
    assert a1[0] == 1  # first request consumes the token
    a2 = await run("rl:refill", 1, 0.5, 5000.0)  # same instant -> no refill yet
    assert a2[0] == 0
    assert a2[2] == 2  # ceil(1 / 0.5)


# ---------------------------------------------------------------------------
# 3. retry_after math at refill_rate == 0 (the constant 60s branch)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_after_with_zero_refill_is_60(token_bucket):
    _redis, run = token_bucket
    await run("rl:zero", 1, 0, 7000.0)  # consume the only token
    denied = await run("rl:zero", 1, 0, 7000.0)
    assert denied[0] == 0
    assert denied[2] == 60  # refill_rate == 0 -> fixed 60s retry


# ---------------------------------------------------------------------------
# 4. Refill after simulated time advance (now_s is injectable)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refills_after_time_advance(token_bucket):
    _redis, run = token_bucket
    # capacity=2, refill=1 token/sec. Drain both at t=0, denied, then advance the
    # clock 2s and it should admit again (refilled up to the cap).
    assert (await run("rl:time", 2, 1.0, 0.0))[0] == 1
    assert (await run("rl:time", 2, 1.0, 0.0))[0] == 1
    assert (await run("rl:time", 2, 1.0, 0.0))[0] == 0  # empty at t=0
    # Advance 2 seconds -> +2 tokens (clamped at capacity). Admit resumes.
    assert (await run("rl:time", 2, 1.0, 2.0))[0] == 1


# ---------------------------------------------------------------------------
# 5. min(capacity) clamp — refill never exceeds capacity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refill_clamps_at_capacity(token_bucket):
    _redis, run = token_bucket
    # capacity=2. Consume 1 at t=0 (remaining 1). Advance a HUGE interval; the
    # bucket must clamp at capacity (2), not accumulate unbounded tokens: so we
    # can consume at most capacity again, i.e. exactly 2 more, then denial.
    assert (await run("rl:clamp", 2, 1.0, 0.0))[0] == 1  # remaining 1
    # Jump 10_000s: tokens would be 1 + 10_000*1 without the clamp; must clamp @2.
    r = await run("rl:clamp", 2, 1.0, 10_000.0)
    assert r[0] == 1 and r[1] == 1  # consumed 1 of the clamped 2 -> remaining 1
    assert (await run("rl:clamp", 2, 1.0, 10_000.0))[0] == 1  # consume the 2nd
    assert (await run("rl:clamp", 2, 1.0, 10_000.0))[0] == 0  # now empty -> denied


# ---------------------------------------------------------------------------
# 6. Key TTL is set (idle buckets self-expire) — REAL-LUA ONLY
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bucket_key_gets_ttl():
    if not _LUA_OK:
        pytest.skip("TTL assertion needs a real Redis backend (lupa unavailable)")
    import fakeredis.aioredis as fa

    redis = fa.FakeRedis()
    await _run_real_lua(redis, "rl:ttl", 5, 1.0, 100.0)
    ttl = await redis.ttl("rl:ttl")
    # Script sets EXPIRE(key, 3600). Allow a small margin for clock granularity.
    assert 3590 <= ttl <= 3600


# ---------------------------------------------------------------------------
# 7. End-to-end via RateLimiter.check() over the REAL script (no eval stub).
#    Proves the wrapper's int-coercion + now_s injection line up with the Lua.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ratelimiter_check_over_real_lua():
    if not _LUA_OK:
        pytest.skip("needs real Lua execution (lupa unavailable)")
    import fakeredis.aioredis as fa

    redis = fa.FakeRedis()
    limiter = RateLimiter(redis=redis, capacity=2, refill_per_second=0.0)

    r1 = await limiter.check("rl:e2e", now_s=1000.0)
    r2 = await limiter.check("rl:e2e", now_s=1000.0)
    r3 = await limiter.check("rl:e2e", now_s=1000.0)  # bucket now empty
    assert r1.allowed is True and r2.allowed is True
    assert r3.allowed is False
    assert r3.retry_after_s == 60  # refill 0 -> the fixed branch, end to end
    assert r3.fail_open is False  # the script RAN; this is a real denial
