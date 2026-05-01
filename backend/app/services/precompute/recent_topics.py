"""§21 Phase 10 — per-IP-hash "recent topics" cache (`AC-PRECOMP-OBJ-4`).

Stores the last `MAX_RECENT` topic slugs per `ip_hash` in Redis. Never
counts toward auth or rate-limit decisions — purely a UX prime for the
landing page.

Redis key: `tk:recent:{ip_hash}` — a list bounded to MAX_RECENT entries.
TTL: `RECENT_TTL_S` (1 day) so abandoned hashes age out cheaply.
"""

from __future__ import annotations

from typing import Any

MAX_RECENT: int = 5
RECENT_TTL_S: int = 86_400
RECENT_KEY_FMT: str = "tk:recent:{ip_hash}"


def _key(ip_hash: str) -> str:
    return RECENT_KEY_FMT.format(ip_hash=ip_hash)


async def push_topic(redis: Any, ip_hash: str, slug: str) -> None:
    """Append `slug` to the front; trim to `MAX_RECENT`. Fail-open."""
    if not redis or not ip_hash or not slug:
        return
    key = _key(ip_hash)
    try:
        # LREM removes any existing copy so the slug surfaces once at the top.
        await redis.lrem(key, 0, slug)
        await redis.lpush(key, slug)
        await redis.ltrim(key, 0, MAX_RECENT - 1)
        await redis.expire(key, RECENT_TTL_S)
    except Exception:
        return


async def get_recent(redis: Any, ip_hash: str) -> list[str]:
    """Return the last-seen-first list (max `MAX_RECENT`). Fail-open → []."""
    if not redis or not ip_hash:
        return []
    try:
        raw = await redis.lrange(_key(ip_hash), 0, MAX_RECENT - 1)
    except Exception:
        return []
    out: list[str] = []
    for item in raw or []:
        if isinstance(item, (bytes, bytearray)):
            out.append(item.decode("utf-8"))
        else:
            out.append(str(item))
    return out
