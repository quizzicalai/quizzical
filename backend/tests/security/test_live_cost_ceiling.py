"""Global daily live-cost circuit breaker (_enforce_global_daily_cost_ceiling).

Cluster-wide hard ceiling on agent-driven quiz starts — bounds AGGREGATE
LLM+FAL spend even against distributed/botnet abuse that the per-IP and
per-session caps can't (they only bound a single source).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.endpoints import quiz as quiz_module


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


@pytest.fixture
def _enabled_cap(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 3, raising=False)
    return cfg


@pytest.mark.asyncio
async def test_allows_up_to_cap(_enabled_cap):
    r = _FakeRedis()
    for _ in range(3):
        await quiz_module._enforce_global_daily_cost_ceiling(r)  # must not raise


@pytest.mark.asyncio
async def test_rejects_beyond_cap_with_503(_enabled_cap):
    r = _FakeRedis()
    for _ in range(3):
        await quiz_module._enforce_global_daily_cost_ceiling(r)
    with pytest.raises(HTTPException) as ei:
        await quiz_module._enforce_global_daily_cost_ceiling(r)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(
        quiz_module.settings.security.live_cost_guard, "enabled", False, raising=False
    )
    r = _FakeRedis()
    for _ in range(50):
        await quiz_module._enforce_global_daily_cost_ceiling(r)
    assert r.store == {}  # counter never touched when disabled


@pytest.mark.asyncio
async def test_zero_cap_is_noop(monkeypatch):
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)
    r = _FakeRedis()
    for _ in range(50):
        await quiz_module._enforce_global_daily_cost_ceiling(r)
    assert r.store == {}


@pytest.mark.asyncio
async def test_fail_open_on_redis_error(_enabled_cap):
    class _BadRedis:
        async def incr(self, key):
            raise RuntimeError("redis down")

        async def expire(self, key, ttl):
            return True

    # Best-effort backstop: a counter fault must not break legitimate traffic.
    await quiz_module._enforce_global_daily_cost_ceiling(_BadRedis())
