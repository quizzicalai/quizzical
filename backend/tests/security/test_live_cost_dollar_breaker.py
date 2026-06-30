"""DOLLAR cost breaker (_enforce_global_daily_cost_ceiling) — Hitlist #2.

The breaker is now a daily $ ceiling read from the cost_meter cents counter. It
gates /quiz/start AND the paid follow-ups /quiz/proceed + /quiz/next
(``is_start=False``). It must FAIL OPEN on any metering/Redis fault.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.endpoints import quiz as quiz_module
from app.services import cost_meter


class _CountingRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + int(amount)
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)


@pytest.fixture
def _budget(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "daily_budget_usd", 1.0, raising=False)  # $1 == 100 cents
    # Disable the secondary count guard so these tests isolate the $ breaker.
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)
    return cfg


@pytest.mark.asyncio
async def test_under_budget_allows_all_endpoints(_budget):
    r = _CountingRedis()
    await cost_meter.record_cents(r, 50)  # 50 of 100 cents
    # All three gate calls must pass (start + the two paid follow-ups).
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)


@pytest.mark.asyncio
async def test_at_or_over_budget_trips_503_on_start(_budget):
    r = _CountingRedis()
    await cost_meter.record_cents(r, 100)  # exactly at budget
    with pytest.raises(HTTPException) as ei:
        await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_budget_also_gates_proceed_and_next(_budget):
    """The dollar breaker gates the PAID follow-ups, not just /start (the old
    count guard only gated starts)."""
    r = _CountingRedis()
    await cost_meter.record_cents(r, 150)  # over budget
    for _ in range(2):
        with pytest.raises(HTTPException) as ei:
            await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)
        assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_fails_open_when_counter_read_raises(_budget):
    """A Redis fault reading the counter must NOT block the quiz (fail-open)."""

    class _BadGet:
        async def get(self, key):
            raise RuntimeError("redis down")

    # No exception even though we can't read the counter.
    await quiz_module._enforce_global_daily_cost_ceiling(_BadGet(), is_start=True)
    await quiz_module._enforce_global_daily_cost_ceiling(_BadGet(), is_start=False)


@pytest.mark.asyncio
async def test_disabled_guard_is_noop(monkeypatch):
    monkeypatch.setattr(
        quiz_module.settings.security.live_cost_guard, "enabled", False, raising=False
    )
    r = _CountingRedis()
    await cost_meter.record_cents(r, 100_000)  # way over any budget
    # Disabled -> never trips regardless of spend.
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=False)


@pytest.mark.asyncio
async def test_zero_budget_disables_dollar_breaker(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "daily_budget_usd", 0.0, raising=False)  # disabled
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)
    r = _CountingRedis()
    await cost_meter.record_cents(r, 100_000)
    await quiz_module._enforce_global_daily_cost_ceiling(r, is_start=True)
