"""§21 Phase 6 — `GET /api/topics/suggest?q=...`.

Public typeahead helper. Hardened per `AC-PRECOMP-SEC-3`:

- `q` length ≥ 2 (else 422).
- max 8 results.
- per-IP rate limit: 60 / minute, fail-open on Redis outage.

The lookup walks the precompute resolver's alias→slug indices first
(cheap exact-prefix), then falls back to `topics.display_name ILIKE`
when needed. Vector NN is intentionally **not** used here — typeahead
must stay on the cheapest path.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session, get_redis_client
from app.models.db import Topic
from app.security.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["topics"])

MIN_Q_LEN = 2
MAX_RESULTS = 8
RATE_LIMIT_PER_MINUTE = 60


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.get("/topics/suggest", summary="Typeahead suggestions for topics")
async def suggest_topics(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[object, Depends(get_redis_client)],
    q: str = Query(..., min_length=1, max_length=80),
) -> dict[str, list[dict[str, str]]]:
    q_norm = q.strip()
    if len(q_norm) < MIN_Q_LEN:
        raise HTTPException(status_code=422, detail="q must be ≥ 2 chars")

    # Per-IP rate limit (fail-open on Redis errors).
    ip = _client_ip(request)
    limiter = RateLimiter(redis=redis, capacity=RATE_LIMIT_PER_MINUTE, refill_per_second=1.0)
    res = await limiter.check(f"topics:suggest:{ip}")
    if not res.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(1, res.retry_after_s))},
        )

    pattern = f"{q_norm}%"
    rows = (
        await db.execute(
            select(Topic.id, Topic.slug, Topic.display_name)
            .where(func.lower(Topic.display_name).like(pattern.lower()))
            .limit(MAX_RESULTS)
        )
    ).all()
    results = [
        {"id": str(r[0]), "slug": r[1], "display_name": r[2]} for r in rows
    ]
    return {"results": results}
