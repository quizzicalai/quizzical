"""P0-1 — per-session hard cap on cost-bearing agent actions.

A single Turnstile-solved /quiz/start must not let a bot drive unbounded paid
LangGraph runs via repeated /quiz/next on one quiz_id. `_enforce_session_action_cap`
bounds combined /proceed + /next actions per session to max_total_questions + 10.
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


@pytest.fixture(autouse=True)
def _fixed_budget(monkeypatch):
    # cap = max_total_questions + 10 = 25
    monkeypatch.setattr(quiz_module.settings.quiz, "max_total_questions", 15, raising=False)


@pytest.mark.asyncio
async def test_allows_up_to_budget():
    r = _FakeRedis()
    for _ in range(25):  # cap == 25
        await quiz_module._enforce_session_action_cap(r, "q1")  # must not raise


@pytest.mark.asyncio
async def test_rejects_beyond_budget():
    r = _FakeRedis()
    for _ in range(25):
        await quiz_module._enforce_session_action_cap(r, "q1")
    with pytest.raises(HTTPException) as ei:
        await quiz_module._enforce_session_action_cap(r, "q1")
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_cap_is_per_quiz_id():
    r = _FakeRedis()
    for _ in range(30):
        try:
            await quiz_module._enforce_session_action_cap(r, "q1")
        except HTTPException:
            pass
    # A different quiz_id is unaffected by q1 exhausting its budget.
    await quiz_module._enforce_session_action_cap(r, "q2")


@pytest.mark.asyncio
async def test_fail_open_on_redis_error():
    class _BadRedis:
        async def incr(self, key):
            raise RuntimeError("redis down")

        async def expire(self, key, ttl):
            return True

    # Best-effort: a counter fault must not break a legitimate quiz.
    await quiz_module._enforce_session_action_cap(_BadRedis(), "q1")
