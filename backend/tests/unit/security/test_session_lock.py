# tests/unit/security/test_session_lock.py
"""§15.4 — Single-flight session lock (AC-LOCK-1..5)."""
from __future__ import annotations

import pytest

from app.security import session_lock as sl

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.eval_calls: list = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    async def eval(self, script, numkeys, key, arg):
        self.eval_calls.append((script[:20], key, arg))
        if self.kv.get(key) == arg:
            del self.kv[key]
            return 1
        return 0


# AC-LOCK-1: free lock -> acquire returns token, release deletes
async def test_acquire_then_release():
    r = _FakeRedis()
    tok = await sl.acquire(r, "sess-1")
    assert tok and tok != sl.FAIL_OPEN_TOKEN
    assert r.kv["qlock:sess-1"] == tok
    await sl.release(r, "sess-1", tok)
    assert "qlock:sess-1" not in r.kv


# AC-LOCK-2: held lock -> second acquire returns None
async def test_second_acquire_returns_none_when_held():
    r = _FakeRedis()
    t1 = await sl.acquire(r, "s")
    assert t1
    t2 = await sl.acquire(r, "s")
    assert t2 is None


# AC-LOCK-4: Redis error -> fail open token
async def test_fail_open_on_redis_error():
    class _Boom:
        async def set(self, *a, **k):
            raise RuntimeError("down")
    tok = await sl.acquire(_Boom(), "s")
    assert tok == sl.FAIL_OPEN_TOKEN


# AC-LOCK-5: token-matched release does nothing if token differs
async def test_release_does_not_delete_other_token():
    r = _FakeRedis()
    r.kv["qlock:s"] = "OTHER"
    await sl.release(r, "s", "MINE")
    assert r.kv["qlock:s"] == "OTHER"


async def test_release_noop_on_failopen_token():
    r = _FakeRedis()
    r.kv["qlock:s"] = "x"
    await sl.release(r, "s", sl.FAIL_OPEN_TOKEN)
    assert r.kv["qlock:s"] == "x"
    assert r.eval_calls == []
