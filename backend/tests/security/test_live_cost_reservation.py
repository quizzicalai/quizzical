"""Budget reservation at admission + Redis-outage local fallback (Hitlist #1/#3).

These exercise ``_enforce_global_daily_cost_ceiling`` end-to-end:

  * #1 — on /start it RESERVES an estimate via INCRBY so concurrent admissions
    see each other (the reservation is reconciled/released by the caller). A
    burst that collectively crosses the ceiling is caught at admission rather
    than overshooting (the old read-only breaker overshot).
  * #1 — the reservation FAILS OPEN on a Redis fault (consistent with the
    read-only breaker today).
  * #3 — when the $ counter is unreadable (Redis down) the /start path consults
    the process-local fallback cap; a sustained outage trips it, and recovery is
    immediate once the read succeeds again.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.endpoints import quiz as quiz_module
from app.services import cost_meter
from app.services import local_fallback_limiter as lfl


class _CountingRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + int(amount)
        return self.store[key]

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - int(amount)
        return self.store[key]

    async def set(self, key: str, value) -> bool:
        self.store[key] = int(value)
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)


@pytest.fixture(autouse=True)
def _clean_local_window():
    lfl.reset()
    yield
    lfl.reset()


@pytest.fixture
def _reserve_budget(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "daily_budget_usd", 1.0, raising=False)  # 100 cents
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)  # isolate $
    # 10-cent reservation per start.
    monkeypatch.setattr(cfg, "reservation_estimate_usd", 0.10, raising=False)
    return cfg


@pytest.mark.asyncio
async def test_reservation_increments_counter_at_admission(_reserve_budget):
    r = _CountingRedis()
    reserved = await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    assert reserved == 10  # $0.10 reserved
    # The reservation is visible to the breaker's counter read immediately.
    assert await cost_meter.read_daily_cents(r) == 10


@pytest.mark.asyncio
async def test_concurrent_admissions_stack_reservations_and_trip_before_overshoot(
    _reserve_budget,
):
    """Each admission reserves 10 cents; the 10th lands the counter exactly at
    the 100-cent budget and is rejected — without a reservation the read-only
    breaker would admit ALL of them (overshoot)."""
    r = _CountingRedis()
    admitted = 0
    rejected = 0
    for _ in range(15):
        try:
            await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
            admitted += 1
        except HTTPException as ei:
            assert ei.status_code == 503
            rejected += 1
    # 9 admissions reserve 90 cents (<100); the 10th reservation hits 100 and is
    # rejected (its reservation released), and everything after stays rejected.
    assert admitted == 9
    assert rejected == 6
    # The rejected request's reservation was released, so the counter sits at the
    # 9 admitted reservations (90 cents), never above the budget.
    assert await cost_meter.read_daily_cents(r) == 90


@pytest.mark.asyncio
async def test_reservation_fails_open_on_redis_fault(_reserve_budget):
    """Consistent with the read-only breaker: a reservation INCRBY fault must
    never block a legitimate quiz."""

    class _ReadOkReserveBad(_CountingRedis):
        async def incrby(self, key, amount):
            raise RuntimeError("redis down")

    r = _ReadOkReserveBad()
    # read returns 0 (under budget), reservation faults -> proceed with 0 reserved.
    reserved = await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    assert reserved == 0


@pytest.mark.asyncio
async def test_proceed_and_next_never_reserve(_reserve_budget):
    r = _CountingRedis()
    reserved = await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)
    assert reserved == 0
    assert await cost_meter.read_daily_cents(r) == 0  # untouched on follow-ups


# --- Hitlist #3 — Redis-outage local fallback -----------------------------


class _RedisDownForReads(_CountingRedis):
    """Counter GET raises (Redis down for the $ counter) but the secondary count
    guard is disabled in these tests, so the local fallback is the only cap."""

    async def get(self, key):
        raise RuntimeError("redis down")


@pytest.fixture
def _outage_budget(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "daily_budget_usd", 50.0, raising=False)
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)
    monkeypatch.setattr(cfg, "redis_outage_local_start_cap", 3, raising=False)
    monkeypatch.setattr(cfg, "redis_outage_local_window_s", 60, raising=False)
    return cfg


@pytest.mark.asyncio
async def test_redis_outage_enforces_local_cap(_outage_budget):
    r = _RedisDownForReads()
    # Within the local cap (3): admitted.
    for _ in range(3):
        await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    # 4th in the window trips the local fallback 503.
    with pytest.raises(HTTPException) as ei:
        await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_local_cap_only_applies_to_start_not_followups(_outage_budget):
    r = _RedisDownForReads()
    # Follow-ups (is_start=False) never consult the local cap, even during an
    # outage — they're already bounded by the per-session action cap upstream.
    for _ in range(20):
        await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)


@pytest.mark.asyncio
async def test_recovers_when_redis_returns(_outage_budget):
    """Once the counter read succeeds again, the breaker uses the real counter
    and the local fallback is no longer consulted (no lingering local block)."""
    down = _RedisDownForReads()
    for _ in range(3):
        await quiz_module._enforce_global_daily_cost_ceiling(down, is_start=True)
    with pytest.raises(HTTPException):
        await quiz_module._enforce_global_daily_cost_ceiling(down, is_start=True)

    # Redis returns (a healthy client). Even though the in-memory window is full,
    # the breaker now reads the real (under-budget) counter and admits.
    healthy = _CountingRedis()
    await quiz_module._enforce_global_daily_cost_ceiling(healthy, is_start=True)
