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

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session, get_redis_client
from app.core.error_codes import QF_INVALID_CATEGORY, QF_RATE_LIMITED
from app.core.errors import coded_http_exception
from app.models.db import Topic
from app.security.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["topics"])

MIN_Q_LEN = 2
MAX_RESULTS = 8
RATE_LIMIT_PER_MINUTE = 60


# Shared trusted-proxy-aware resolver (see app.security.rate_limit).
from app.security.rate_limit import _client_ip  # noqa: E402


@router.get("/topics/suggest", summary="Typeahead suggestions for topics")
async def suggest_topics(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[object, Depends(get_redis_client)],
    q: str = Query(..., min_length=1, max_length=80),
) -> dict[str, list[dict[str, str]]]:
    q_norm = q.strip()
    if len(q_norm) < MIN_Q_LEN:
        raise coded_http_exception(
            status_code=422, detail="q must be ≥ 2 chars", code=QF_INVALID_CATEGORY
        )

    # Per-IP rate limit (fail-open on Redis errors).
    ip = _client_ip(request)
    limiter = RateLimiter(redis=redis, capacity=RATE_LIMIT_PER_MINUTE, refill_per_second=1.0)
    res = await limiter.check(f"topics:suggest:{ip}")
    if not res.allowed:
        raise coded_http_exception(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            code=QF_RATE_LIMITED,
            headers={"Retry-After": str(max(1, res.retry_after_s))},
        )

    # AC-PRECOMP-SEC (deep-review #23) — escape LIKE metacharacters in the raw
    # user input BEFORE composing the prefix pattern. Without this, a `q` of "%"
    # (or "_") becomes a wildcard that matches EVERY row: a public, per-keystroke
    # endpoint turned into a full-table scan (trivial DoS amplifier). We escape
    # the escape char first, then `%` and `_`, and tell the DB the escape char via
    # `escape="\\"` so the wildcards are treated literally.
    escaped = (
        q_norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"{escaped}%"  # trailing % is the (intentional) prefix wildcard
    rows = (
        await db.execute(
            select(Topic.id, Topic.slug, Topic.display_name)
            .where(func.lower(Topic.display_name).like(pattern.lower(), escape="\\"))
            .limit(MAX_RESULTS)
        )
    ).all()
    results = [
        {"id": str(r[0]), "slug": r[1], "display_name": r[2]} for r in rows
    ]
    return {"results": results}
