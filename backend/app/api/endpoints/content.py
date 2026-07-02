"""§21 Phase 6 — `POST /api/content/flag`.

User-submitted content flags recorded as feedback (`AC-PRECOMP-FLAG-1..3`,
`AC-PRECOMP-SEC-7`).

Content safety / moderation is owned by the third-party providers (OpenAI,
Google, fal.ai); Quafel applies no home-grown content moderation. A flag is
therefore recorded purely as operator feedback — it NEVER auto-pulls or
quarantines content. The anti-abuse machinery around recording a flag is kept.

Flow:
1. Validate `reason_code`. Honeypot codes silent-drop with a `204` so
   scanners can't distinguish them from accepted submissions
   (`AC-PRECOMP-SEC-7`). Unknown codes → 422.
2. Hash the requester IP (HMAC-SHA256) — never store the raw IP
   (`AC-PRECOMP-FLAG-3`).
3. Abuse check: > 50 distinct targets in 24 h from the same ip_hash →
   shadow-discard with `204` (no DB row written).
4. PII-scrub + clamp `reason_text` (`AC-PRECOMP-FLAG-2`).
5. Persist the `content_flags` row (feedback only).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.config import settings
from app.models.db import ContentFlag
from app.services.precompute.flag_aggregator import (
    clamp_reason_text,
    hash_ip,
    is_abusive_ip,
    validate_reason_code,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["content"])


TARGET_KINDS = ("topic_pack", "character", "media_asset", "question")


class FlagRequest(BaseModel):
    target_kind: Literal["topic_pack", "character", "media_asset", "question"]
    target_id: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=32)
    reason_text: str | None = Field(default=None, max_length=10_000)


# Use the shared, trusted-proxy-aware client-IP resolver. Trusting the
# left-most X-Forwarded-For hop here would let one attacker rotate the header
# to look like N distinct IPs and evade the per-ip_hash abuse cap.
from app.security.rate_limit import _client_ip  # noqa: E402


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

    # 5. Persist row as feedback. Recording a flag NEVER auto-pulls or
    # quarantines content — content safety is enforced by the third-party
    # providers, not by a Quafel-side moderation loop.
    flag = ContentFlag(
        target_kind=body.target_kind,
        target_id=body.target_id,
        reason_code=body.reason_code,
        reason_text=clean_text,
        client_ip_hash=ip_hash,
    )
    db.add(flag)
    await db.flush()

    await db.commit()
    return {"status": "accepted"}
