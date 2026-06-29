# tests/unit/services/test_llm_concurrency_cluster.py
"""Tests for the OPTIONAL cluster-wide (Redis) LLM concurrency cap (P1).

These exercise the Redis-backed ``_ClusterConcurrencyGate`` layered on top of
the in-process ``asyncio.Semaphore`` in ``app.services.llm_concurrency``.

Matrix per the fix spec:
  * enabled + under cap  → acquires a cluster slot.
  * enabled + at cap     → waits, then (on timeout) falls back to local-only.
  * Redis error          → fails open to the in-process semaphore (still works).
  * disabled             → identical to current behaviour (no Redis touched).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.llm_concurrency import (
    GLOBAL_CONCURRENCY_ACQUIRE_LUA,
    GLOBAL_CONCURRENCY_RELEASE_LUA,
    LLMConcurrencyLimiter,
    _ClusterConcurrencyGate,
)


class FakeRedis:
    """Minimal in-memory Redis double implementing ``eval`` for our two Lua
    scripts. It models the GET/INCR/DECR/DEL/EXPIRE semantics they rely on so
    the gate's acquire/release accounting can be asserted exactly.
    """

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.eval_calls = 0

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self.eval_calls += 1
        key = args[0]
        if script == GLOBAL_CONCURRENCY_ACQUIRE_LUA:
            capacity = int(args[1])
            current = int(self.store.get(key, 0))
            if current < capacity:
                current += 1
                self.store[key] = current
                return [1, current]
            return [0, current]
        if script == GLOBAL_CONCURRENCY_RELEASE_LUA:
            current = int(self.store.get(key, 0))
            if current <= 1:
                self.store.pop(key, None)
                return 0
            current -= 1
            self.store[key] = current
            return current
        raise AssertionError(f"unexpected script: {script[:40]!r}")


class BrokenRedis:
    """Redis double whose ``eval`` always raises — exercises fail-open."""

    def __init__(self) -> None:
        self.eval_calls = 0

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        self.eval_calls += 1
        raise RuntimeError("redis is down")


def _gate(redis: Any, *, capacity: int, timeout_s: float = 0.0) -> _ClusterConcurrencyGate:
    return _ClusterConcurrencyGate(
        redis_factory=lambda: redis,
        capacity=capacity,
        namespace="test:llm:concurrency",
        acquire_timeout_s=timeout_s,
        poll_interval_s=0.01,
    )


# ---------------------------------------------------------------------------
# enabled + under cap → acquires
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enabled_under_cap_acquires_cluster_slot() -> None:
    redis = FakeRedis()
    gate = _gate(redis, capacity=4)
    limiter = LLMConcurrencyLimiter(
        capacity=4, acquire_timeout_s=1.0, cluster_gate=gate
    )

    assert limiter.cluster_enabled is True

    async with limiter.acquire(tool="t"):
        # In-process AND cluster slots are both held.
        assert limiter.metrics()["in_flight"] == 1
        assert redis.store[gate._key] == 1

    # Both released on exit.
    m = limiter.metrics()
    assert m["in_flight"] == 0
    assert m["total_cluster_acquired"] == 1
    assert m["total_cluster_fallbacks"] == 0
    assert gate._key not in redis.store  # counter cleaned up at 0


@pytest.mark.asyncio
async def test_gate_acquire_release_roundtrip() -> None:
    redis = FakeRedis()
    gate = _gate(redis, capacity=2)

    assert await gate.acquire(tool="a") is True
    assert await gate.acquire(tool="b") is True
    assert redis.store[gate._key] == 2

    await gate.release(tool="a")
    assert redis.store[gate._key] == 1
    await gate.release(tool="b")
    assert gate._key not in redis.store


# ---------------------------------------------------------------------------
# enabled + at cap → waits / falls back per timeout semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enabled_at_cap_no_wait_falls_back_local_only() -> None:
    """At the cluster cap with no wait budget, acquire falls back (False) so the
    request still proceeds under the local semaphore — never blocked."""
    redis = FakeRedis()
    gate = _gate(redis, capacity=1, timeout_s=0.0)

    # Pre-fill the single cluster slot held by another replica.
    assert await gate.acquire(tool="other") is True
    assert redis.store[gate._key] == 1

    # A larger local capacity so the local semaphore is NOT the limiter here.
    limiter = LLMConcurrencyLimiter(
        capacity=8, acquire_timeout_s=1.0, cluster_gate=gate
    )

    async with limiter.acquire(tool="t"):
        # Local slot acquired; cluster slot was saturated → fell back.
        assert limiter.metrics()["in_flight"] == 1
        # Counter unchanged (still just the pre-existing holder).
        assert redis.store[gate._key] == 1

    m = limiter.metrics()
    assert m["total_cluster_acquired"] == 0
    assert m["total_cluster_fallbacks"] == 1
    # Pre-existing holder's slot is untouched by the fallback path.
    assert redis.store[gate._key] == 1


@pytest.mark.asyncio
async def test_enabled_at_cap_waits_then_acquires_when_slot_frees() -> None:
    """With a wait budget, a saturated acquire blocks until a slot frees."""
    redis = FakeRedis()
    gate = _gate(redis, capacity=1, timeout_s=1.0)

    assert await gate.acquire(tool="holder") is True
    assert redis.store[gate._key] == 1

    async def _free_after_delay() -> None:
        await asyncio.sleep(0.05)
        await gate.release(tool="holder")

    freer = asyncio.create_task(_free_after_delay())
    # This should poll-wait until the holder releases, then succeed.
    acquired = await gate.acquire(tool="waiter")
    await freer

    assert acquired is True
    assert redis.store[gate._key] == 1  # waiter now holds the single slot


@pytest.mark.asyncio
async def test_enabled_at_cap_wait_timeout_falls_back() -> None:
    """If the slot never frees within the wait budget, acquire returns False
    (fall back to local-only) rather than raising/blocking forever."""
    redis = FakeRedis()
    gate = _gate(redis, capacity=1, timeout_s=0.08)

    assert await gate.acquire(tool="holder") is True

    acquired = await gate.acquire(tool="waiter")
    assert acquired is False
    # Holder still holds; waiter did not sneak in.
    assert redis.store[gate._key] == 1


# ---------------------------------------------------------------------------
# Redis error → falls back to in-process (still works)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_error_acquire_fails_open() -> None:
    redis = BrokenRedis()
    gate = _gate(redis, capacity=4, timeout_s=1.0)
    assert await gate.acquire(tool="t") is False
    assert redis.eval_calls == 1  # tried once, then failed open


@pytest.mark.asyncio
async def test_limiter_with_broken_redis_still_works() -> None:
    redis = BrokenRedis()
    gate = _gate(redis, capacity=4, timeout_s=1.0)
    limiter = LLMConcurrencyLimiter(
        capacity=2, acquire_timeout_s=1.0, cluster_gate=gate
    )

    async with limiter.acquire(tool="t"):
        # Local semaphore still bounds; cluster cap failed open.
        assert limiter.metrics()["in_flight"] == 1

    m = limiter.metrics()
    assert m["in_flight"] == 0
    assert m["total_acquired"] == 1
    assert m["total_cluster_acquired"] == 0
    assert m["total_cluster_fallbacks"] == 1


@pytest.mark.asyncio
async def test_no_redis_available_behaves_like_local_only() -> None:
    """A factory that yields no client → cluster path is a no-op (today's behavior)."""
    gate = _ClusterConcurrencyGate(
        redis_factory=lambda: None,
        capacity=4,
        namespace="test:llm:concurrency",
        acquire_timeout_s=1.0,
        poll_interval_s=0.01,
    )
    assert await gate.acquire(tool="t") is False
    # release is a silent no-op
    await gate.release(tool="t")


@pytest.mark.asyncio
async def test_factory_raising_fails_open() -> None:
    def _boom() -> Any:
        raise RuntimeError("pool not ready")

    gate = _ClusterConcurrencyGate(
        redis_factory=_boom,
        capacity=4,
        namespace="test:llm:concurrency",
        acquire_timeout_s=1.0,
        poll_interval_s=0.01,
    )
    assert await gate.acquire(tool="t") is False


# ---------------------------------------------------------------------------
# disabled → identical to current behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_does_not_touch_redis() -> None:
    redis = FakeRedis()
    # No cluster_gate → cluster path entirely absent.
    limiter = LLMConcurrencyLimiter(capacity=2, acquire_timeout_s=1.0)

    assert limiter.cluster_enabled is False

    async with limiter.acquire(tool="t"):
        assert limiter.metrics()["in_flight"] == 1

    m = limiter.metrics()
    assert m["in_flight"] == 0
    assert m["total_acquired"] == 1
    assert m["cluster_enabled"] is False
    assert m["total_cluster_acquired"] == 0
    assert m["total_cluster_fallbacks"] == 0
    # Redis was never constructed/used.
    assert redis.eval_calls == 0
    assert redis.store == {}


@pytest.mark.asyncio
async def test_cluster_slot_released_on_inner_exception() -> None:
    """Inner exception must release BOTH the local and the cluster slot."""
    redis = FakeRedis()
    gate = _gate(redis, capacity=2)
    limiter = LLMConcurrencyLimiter(
        capacity=2, acquire_timeout_s=1.0, cluster_gate=gate
    )

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with limiter.acquire(tool="t"):
            assert redis.store[gate._key] == 1
            raise _Boom("inner failure")

    assert limiter.metrics()["in_flight"] == 0
    assert gate._key not in redis.store  # cluster slot released


# ---------------------------------------------------------------------------
# Settings wiring: enabled flag controls construction
# ---------------------------------------------------------------------------

def test_build_cluster_gate_respects_enabled_flag() -> None:
    from app.core.config import GlobalLLMConcurrencyConfig, LLMGlobals
    from app.services.llm_concurrency import _build_cluster_gate

    off = LLMGlobals(global_concurrency=GlobalLLMConcurrencyConfig(enabled=False))
    assert _build_cluster_gate(off) is None

    on = LLMGlobals(
        global_concurrency=GlobalLLMConcurrencyConfig(
            enabled=True, max_concurrent=32, namespace="ns:test"
        )
    )
    gate = _build_cluster_gate(on)
    assert gate is not None
    assert gate._capacity == 32
    assert gate._key == "ns:test:slots"


def test_build_limiter_disabled_by_default() -> None:
    """Default settings keep the cluster cap OFF (safe, opt-in change)."""
    from app.services.llm_concurrency import _build_limiter_from_settings

    limiter = _build_limiter_from_settings()
    assert limiter.cluster_enabled is False


def test_global_concurrency_config_validation() -> None:
    from app.core.config import GlobalLLMConcurrencyConfig

    with pytest.raises(ValueError):
        GlobalLLMConcurrencyConfig(max_concurrent=0)
    with pytest.raises(ValueError):
        GlobalLLMConcurrencyConfig(acquire_timeout_s=-1.0)
    with pytest.raises(ValueError):
        GlobalLLMConcurrencyConfig(poll_interval_s=0.0)
    with pytest.raises(ValueError):
        GlobalLLMConcurrencyConfig(namespace="bad space!")
    # Valid one round-trips.
    cfg = GlobalLLMConcurrencyConfig(
        enabled=True, max_concurrent=10, namespace="a:b-c_d"
    )
    assert cfg.enabled is True
    assert cfg.max_concurrent == 10
