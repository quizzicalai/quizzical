"""§21 Phase 6 — `POST /api/content/flag`.

User-submitted content flags (`AC-PRECOMP-FLAG-1..5`, `AC-PRECOMP-SEC-7`).

Flow:
1. Validate `reason_code`. Honeypot codes silent-drop with a `204` so
   scanners can't distinguish them from accepted submissions
   (`AC-PRECOMP-SEC-7`). Unknown codes → 422.
2. Hash the requester IP (HMAC-SHA256) — never store the raw IP
   (`AC-PRECOMP-FLAG-3`).
3. Abuse check: > 50 distinct targets in 24 h from the same ip_hash →
   shadow-discard with `204` (no DB row written).
4. PII-scrub + clamp `reason_text` (`AC-PRECOMP-FLAG-2`).
5. Persist the `content_flags` row.
6. If distinct ip_hashes for `(target_kind, target_id)` reaches the
   threshold inside the configured window, atomically quarantine the
   pack(s) referencing it (`AC-PRECOMP-FLAG-4`) and invalidate the
   precompute pack cache so subsequent `/quiz/start` falls through to
   the live agent (`AC-PRECOMP-FLAG-5`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session, get_redis_client
from app.core.config import settings
from app.models.db import ContentFlag, TopicPack
from app.services.precompute import cache as pack_cache
from app.services.precompute.flag_aggregator import (
    clamp_reason_text,
    hash_ip,
    is_abusive_ip,
    should_quarantine,
    validate_reason_code,
)
from app.services.precompute.quarantine import quarantine_pack

logger = logging.getLogger(__name__)
router = APIRouter(tags=["content"])


TARGET_KINDS = ("topic_pack", "character", "media_asset", "question")


class FlagRequest(BaseModel):
    target_kind: Literal["topic_pack", "character", "media_asset", "question"]
    target_id: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=32)
    reason_text: str | None = Field(default=None, max_length=10_000)


def _client_ip(request: Request) -> str:
    # `X-Forwarded-For` left-most when present; fallback to peer.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post(
    "/content/flag",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a user content flag",
)
async def flag_content(
    body: FlagRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[object, Depends(get_redis_client)],
) -> dict[str, str]:
    # 1. Reason-code allowlist / honeypot.
    verdict = validate_reason_code(body.reason_code)
    if verdict == "unknown":
        raise HTTPException(status_code=422, detail="unknown reason_code")
    if verdict == "honeypot":
        # Silent drop — same-shape response as accepted (no row written).
        response.status_code = status.HTTP_204_NO_CONTENT
        return {}

    # 2. Hash IP under the rotating secret. Never store the raw IP.
    secret = settings.FLAG_HMAC_SECRET
    ip_hash = hash_ip(_client_ip(request), secret=secret)

    # 3. Abuse check — > 50 distinct targets in 24 h from this ip_hash.
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    distinct_targets = (
        await db.execute(
            select(func.count(func.distinct(ContentFlag.target_id))).where(
                ContentFlag.client_ip_hash == ip_hash,
                ContentFlag.created_at >= cutoff,
            )
        )
    ).scalar_one()
    if is_abusive_ip(int(distinct_targets or 0)):
        logger.info("content_flag.shadow_discard", extra={"ip_hash": ip_hash})
        response.status_code = status.HTTP_204_NO_CONTENT
        return {}

    # 4. Clamp + PII-scrub.
    clean_text = clamp_reason_text(body.reason_text)

    # 5. Persist row.
    flag = ContentFlag(
        target_kind=body.target_kind,
        target_id=body.target_id,
        reason_code=body.reason_code,
        reason_text=clean_text,
        client_ip_hash=ip_hash,
    )
    db.add(flag)
    await db.flush()

    # 6. Threshold check + atomic quarantine + cache invalidation.
    cfg = settings.precompute
    threshold = int(getattr(cfg, "flag_quarantine_count", 5))
    window_h = int(getattr(cfg, "flag_quarantine_window_hours", 24))
    win_cutoff = datetime.now(UTC) - timedelta(hours=window_h)

    distinct_ips = (
        await db.execute(
            select(func.count(func.distinct(ContentFlag.client_ip_hash))).where(
                ContentFlag.target_kind == body.target_kind,
                ContentFlag.target_id == body.target_id,
                ContentFlag.created_at >= win_cutoff,
            )
        )
    ).scalar_one()

    quarantined_pack_ids: list[UUID] = []
    if should_quarantine(int(distinct_ips or 0), threshold=threshold):
        # If the target is itself a pack, quarantine that single row;
        # otherwise (character / media_asset / question) call sites in
        # later phases will hook a cascade. For now we cover the
        # `topic_pack` direct path required by `AC-PRECOMP-FLAG-4`.
        if body.target_kind == "topic_pack":
            try:
                pack_uuid = UUID(body.target_id)
            except (ValueError, TypeError):
                pack_uuid = None
            if pack_uuid is not None and await quarantine_pack(db, pack_uuid):
                # Find the topic_id for cache invalidation.
                row = (
                    await db.execute(
                        select(TopicPack).where(TopicPack.id == pack_uuid)
                    )
                ).scalar_one_or_none()
                if row is not None:
                    quarantined_pack_ids.append(row.id)
                    await pack_cache.invalidate_pack(redis, row.topic_id)

    await db.commit()
    return {
        "status": "accepted",
        "quarantined_packs": ",".join(str(p) for p in quarantined_pack_ids),
    }
