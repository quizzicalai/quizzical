"""§21 Phase 10 — pack hit/miss telemetry surfaced at `/healthz/precompute`.

Lightweight Redis-backed counters with a 24-hour rolling window.

Keys:
- `tk:pc:hits:{bucket}`    INCR per hit, TTL 25 h
- `tk:pc:misses:{bucket}`  INCR per miss, TTL 25 h
- `tk:pc:miss_topics:{bucket}` ZSET INCRBY per miss (member = topic slug),
                                TTL 25 h — drives `top_misses_24h`.

`bucket` is the UTC hour string `YYYYMMDDHH`. We sum the trailing 24
buckets to get the 24-h figure. Single-instance only (good enough for an
operator dashboard); cross-replica accuracy is not a goal.

Fail-open everywhere — telemetry must never break a request path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

_KEY_HITS = "tk:pc:hits:{bucket}"
_KEY_MISSES = "tk:pc:misses:{bucket}"
_KEY_TOPICS = "tk:pc:miss_topics:{bucket}"

_TTL_S = 25 * 3600
_TOP_N_DEFAULT = 10


def _bucket(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.strftime("%Y%m%d%H")


def _trailing_buckets(now: datetime | None = None, *, hours: int = 24) -> list[str]:
    n = now or datetime.now(timezone.utc)
    return [(n - timedelta(hours=i)).strftime("%Y%m%d%H") for i in range(hours)]


async def record_hit(redis: Any, *, now: datetime | None = None) -> None:
    if not redis:
        return
    key = _KEY_HITS.format(bucket=_bucket(now))
    try:
        await redis.incr(key)
        await redis.expire(key, _TTL_S)
    except Exception:
        return


async def record_miss(
    redis: Any, *, topic_slug: str | None = None, now: datetime | None = None
) -> None:
    if not redis:
        return
    bucket = _bucket(now)
    try:
        await redis.incr(_KEY_MISSES.format(bucket=bucket))
        await redis.expire(_KEY_MISSES.format(bucket=bucket), _TTL_S)
        if topic_slug:
            zkey = _KEY_TOPICS.format(bucket=bucket)
            await redis.zincrby(zkey, 1, topic_slug)
            await redis.expire(zkey, _TTL_S)
    except Exception:
        return


async def _sum_buckets(redis: Any, key_fmt: str, buckets: list[str]) -> int:
    total = 0
    for b in buckets:
        try:
            v = await redis.get(key_fmt.format(bucket=b))
        except Exception:
            v = None
        if v is None:
            continue
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8")
        try:
            total += int(v)
        except (TypeError, ValueError):
            continue
    return total


async def get_24h_snapshot(
    redis: Any, *, now: datetime | None = None, top_n: int = _TOP_N_DEFAULT
) -> dict:
    if not redis:
        return {
            "hits_24h": 0, "misses_24h": 0,
            "hit_rate_24h": 0.0, "miss_rate_24h": 0.0,
            "top_misses_24h": [],
        }
    buckets = _trailing_buckets(now)
    hits = await _sum_buckets(redis, _KEY_HITS, buckets)
    misses = await _sum_buckets(redis, _KEY_MISSES, buckets)
    total = hits + misses
    hit_rate = hits / total if total else 0.0

    # Top-N misses across the 24-h window: aggregate ZSETs in Python.
    tally: dict[str, float] = {}
    for b in buckets:
        try:
            rows = await redis.zrange(
                _KEY_TOPICS.format(bucket=b), 0, -1, withscores=True
            )
        except Exception:
            continue
        for member, score in rows or []:
            slug = member.decode("utf-8") if isinstance(member, (bytes, bytearray)) else str(member)
            try:
                tally[slug] = tally.get(slug, 0.0) + float(score)
            except (TypeError, ValueError):
                continue
    top = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    return {
        "hits_24h": hits,
        "misses_24h": misses,
        "hit_rate_24h": round(hit_rate, 4),
        "miss_rate_24h": round(1.0 - hit_rate, 4) if total else 0.0,
        "top_misses_24h": [{"slug": s, "count": int(c)} for s, c in top],
    }


__all__ = ["record_hit", "record_miss", "get_24h_snapshot"]
