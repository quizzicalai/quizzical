# app/security/session_lock.py
"""§15.4 — Single-flight session lock backed by Redis SET NX EX.

Used to prevent two concurrent /quiz/next requests for the same session from
racing on `last_served_index` / `quiz_history`.

- ``acquire`` returns a token string when the lock was taken, ``None`` if held
  by someone else, and (per AC-LOCK-4) returns a synthetic ``__failopen__``
  token when Redis is unreachable so the caller can proceed.
- ``release`` is a token-matched DEL via Lua (AC-LOCK-5) — never deletes a
  lock acquired by a later request after our TTL expired.
"""
from __future__ import annotations

import secrets
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

FAIL_OPEN_TOKEN = "__failopen__"

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
else
  return 0
end
"""


def _key(session_id: str) -> str:
    return f"qlock:{session_id}"


async def acquire(redis, session_id: str, *, ttl_s: int = 10) -> Optional[str]:
    token = secrets.token_hex(8)
    try:
        # `nx=True, ex=ttl_s` matches redis-py async API.
        ok = await redis.set(_key(session_id), token, nx=True, ex=int(ttl_s))
    except Exception as e:
        logger.warning("session_lock.fail_open", error=str(e), session_id=session_id)
        return FAIL_OPEN_TOKEN
    return token if ok else None


async def release(redis, session_id: str, token: str) -> None:
    if not token or token == FAIL_OPEN_TOKEN:
        return
    try:
        await redis.eval(_RELEASE_LUA, 1, _key(session_id), token)
    except Exception:
        # best-effort
        pass
