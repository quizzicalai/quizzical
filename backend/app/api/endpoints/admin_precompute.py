"""§21 Phase 3 — operator-only precompute admin endpoints.

All routes require `Depends(require_operator)` (bearer + 2FA-in-prod) and
write append-only `audit_log` rows for every mutation. Bodies and headers
are validated by Pydantic models; nothing is logged that contains the
operator token.

Endpoints:
- `POST   /admin/precompute/jobs`        — enqueue (`AC-PRECOMP-PROMOTE-1`)
- `GET    /admin/precompute/jobs`        — list (filter by status/topic)
- `POST   /admin/precompute/promote`     — manual atomic promote
- `POST   /admin/precompute/rollback`    — set `topics.current_pack_id` back
- `GET    /admin/precompute/cost`        — today's spend snapshot
- `POST   /admin/precompute/users/forget`— GDPR right-to-erasure trigger
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    OperatorPrincipal,
    get_db_session,
    require_operator,
)
from app.core.config import settings
from app.models.db import PrecomputeJob, Topic, TopicPack
from app.services.precompute import audit, cost, cost_guard, jobs, safety

logger = structlog.get_logger("app.api.admin.precompute")

router = APIRouter(prefix="/admin/precompute", tags=["admin", "precompute"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EnqueueRequest(BaseModel):
    topic_id: UUID

    @field_validator("topic_id", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, UUID):
            return v
        try:
            return UUID(str(v))
        except (TypeError, ValueError) as exc:
            raise ValueError("topic_id must be a UUID") from exc


class PromoteRequest(BaseModel):
    topic_id: UUID
    pack_id: UUID


class RollbackRequest(BaseModel):
    topic_id: UUID
    to_pack_id: UUID


class ForgetRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class JobView(BaseModel):
    id: UUID
    topic_id: UUID
    status: str
    attempt: int
    tier: str | None
    cost_cents: int
    error_text: str | None


class CostView(BaseModel):
    spent_cents: int
    daily_cap_cents: int
    tier3_cap_cents: int
    remaining_cents: int
    topics_30d: list[dict] = []
    """`AC-PRECOMP-COST-4` — per-topic spend over the trailing 30 days."""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/jobs", status_code=status.HTTP_201_CREATED, response_model=JobView)
async def enqueue_job(
    body: EnqueueRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    actor: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> JobView:
    topic = (
        await db.execute(select(Topic).where(Topic.id == body.topic_id).limit(1))
    ).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=404, detail="topic not found")
    try:
        safety.assert_topic_can_be_enqueued(
            policy_status=topic.policy_status, topic_id=str(topic.id),
        )
    except safety.TopicBannedError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc

    row = await jobs.enqueue(db, topic_id=topic.id)
    await audit.record_operator_action(
        db,
        actor_id=actor.actor_id, action="precompute.enqueue",
        target_kind="topic", target_id=str(topic.id),
        after={"job_id": str(row.id), "status": row.status},
    )
    await db.commit()
    return _to_job_view(row)


@router.get("/jobs", response_model=list[JobView])
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    _: Annotated[OperatorPrincipal, Depends(require_operator)],
    status_filter: str | None = Query(default=None, alias="status"),
    topic_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[JobView]:
    q = select(PrecomputeJob)
    if status_filter:
        try:
            jobs.JobStatus(status_filter)  # validate
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid status filter") from exc
        q = q.where(PrecomputeJob.status == status_filter)
    if topic_id is not None:
        q = q.where(PrecomputeJob.topic_id == topic_id)
    q = q.order_by(PrecomputeJob.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [_to_job_view(r) for r in rows]


@router.post("/promote", response_model=JobView)
async def promote_pack(
    body: PromoteRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    actor: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> JobView:
    topic = (
        await db.execute(select(Topic).where(Topic.id == body.topic_id).limit(1))
    ).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=404, detail="topic not found")
    pack = (
        await db.execute(
            select(TopicPack).where(
                TopicPack.id == body.pack_id, TopicPack.topic_id == body.topic_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="pack not found for topic")
    if pack.status != "published":
        raise HTTPException(status_code=409, detail="pack not in published state")

    before = {"current_pack_id": str(topic.current_pack_id) if topic.current_pack_id else None}
    topic.current_pack_id = pack.id
    after = {"current_pack_id": str(pack.id)}

    await audit.record_operator_action(
        db,
        actor_id=actor.actor_id, action="precompute.promote",
        target_kind="topic", target_id=str(topic.id),
        before=before, after=after,
    )
    await db.commit()

    # Synthesize a job-shaped response so the operator client has a uniform
    # surface; the real promotion happens via the FK update above.
    fake = PrecomputeJob(
        id=UUID(int=0), topic_id=topic.id, status="succeeded",
        attempt=0, tier=None, cost_cents=0, error_text=None,
    )
    return _to_job_view(fake)


@router.post("/rollback", response_model=JobView)
async def rollback_pack(
    body: RollbackRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    actor: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> JobView:
    topic = (
        await db.execute(select(Topic).where(Topic.id == body.topic_id).limit(1))
    ).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=404, detail="topic not found")
    target_pack = (
        await db.execute(
            select(TopicPack).where(
                TopicPack.id == body.to_pack_id, TopicPack.topic_id == body.topic_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if target_pack is None:
        raise HTTPException(status_code=404, detail="target pack not found")
    if target_pack.status not in {"published", "deprecated"}:
        raise HTTPException(status_code=409, detail="target pack not eligible")

    before = {"current_pack_id": str(topic.current_pack_id) if topic.current_pack_id else None}
    topic.current_pack_id = target_pack.id
    after = {"current_pack_id": str(target_pack.id)}

    await audit.record_operator_action(
        db,
        actor_id=actor.actor_id, action="precompute.rollback",
        target_kind="topic", target_id=str(topic.id),
        before=before, after=after,
    )
    await db.commit()
    fake = PrecomputeJob(
        id=UUID(int=0), topic_id=topic.id, status="succeeded",
        attempt=0, tier=None, cost_cents=0, error_text=None,
    )
    return _to_job_view(fake)


@router.get("/cost", response_model=CostView)
async def get_cost(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    _: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> CostView:
    cfg = getattr(settings, "precompute", None)
    daily_usd = float(getattr(cfg, "daily_budget_usd", 5.0) or 5.0)
    pct = float(getattr(cfg, "tier3_budget_pct", 0.75) or 0.75)
    snap = await cost_guard.snapshot(
        db, daily_budget_usd=daily_usd, tier3_budget_pct=pct,
    )
    topics_30d = await cost.topic_cost_30d(db)
    return CostView(
        spent_cents=snap.spent_cents,
        daily_cap_cents=snap.daily_cap_cents,
        tier3_cap_cents=snap.tier3_cap_cents,
        remaining_cents=snap.remaining_cents,
        topics_30d=topics_30d,
    )


@router.post("/users/forget", status_code=status.HTTP_202_ACCEPTED)
async def forget_user(
    body: ForgetRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    actor: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> dict[str, str]:
    """Record a GDPR erasure request and scrub linked flag rows.

    `AC-PRECOMP-SEC-8` — content_flags rows whose `client_ip_hash`
    matches `hash_ip(user_id)` (i.e. the same HMAC the public flag
    endpoint computes from the requester IP) are anonymised in-place:
    `client_ip_hash="DELETED"` and `reason_text=NULL`. The row stays
    for audit/aggregation, but no further linkage to the user remains.
    """
    from sqlalchemy import update as _update

    from app.core.config import settings as _settings
    from app.models.db import ContentFlag
    from app.services.precompute.flag_aggregator import hash_ip

    # The user_id is treated as the same input the IP-hashing path
    # consumes; this lets a forget request anonymise rows whose ip_hash
    # was derived from the user_id (e.g. opaque session ids).
    user_hash = hash_ip(body.user_id, secret=_settings.FLAG_HMAC_SECRET)
    res = await db.execute(
        _update(ContentFlag)
        .where(ContentFlag.client_ip_hash == user_hash)
        .values(client_ip_hash="DELETED", reason_text=None)
    )
    scrubbed = int(res.rowcount or 0)

    await audit.record_operator_action(
        db,
        actor_id=actor.actor_id, action="precompute.user_forget",
        target_kind="user", target_id=body.user_id,
        after={"requested": True, "scrubbed_flag_rows": scrubbed},
    )
    await db.commit()
    return {"status": "accepted", "user_id": body.user_id, "scrubbed": str(scrubbed)}


# ---------------------------------------------------------------------------
# Starter pack import (operator-only) — §21 Phase 9
# ---------------------------------------------------------------------------


class ImportPacksResult(BaseModel):
    """Counters returned by `scripts.import_packs.import_archive`."""

    packs_inserted: int
    packs_skipped: int
    skipped_db_not_empty: int


@router.post(
    "/import",
    status_code=status.HTTP_200_OK,
    response_model=ImportPacksResult,
    summary="Import a signed starter-pack archive (operator-only).",
)
async def import_starter_packs(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    actor: Annotated[OperatorPrincipal, Depends(require_operator)],
    force_upgrade: bool = Query(
        default=False,
        alias="force_upgrade",
        description=(
            "When true, bypass the AC-PRECOMP-OBJ-2 'skip if DB already has any "
            "published pack' gate. Per-pack idempotency on (topic_id, version) "
            "still prevents duplicates, so this is safe for re-seeding prod."
        ),
    ),
) -> ImportPacksResult:
    """Accept a raw signed starter-pack archive in the request body.

    Headers:
      - ``Authorization: Bearer <OPERATOR_TOKEN>``  — required (gateway).
      - ``X-Archive-Signature: <hex hmac-sha256>``  — required.

    Body: raw archive bytes (``application/octet-stream``); the same bytes
    that were hashed for the signature. MUST verify against
    ``settings.PRECOMPUTE_HMAC_SECRET`` before any DB write.

    `AC-PRECOMP-SEC-5` — unsigned / mismatched archives are refused with
    HTTP 401. `AC-PRECOMP-OBJ-2` — a non-empty DB returns counters with
    ``skipped_db_not_empty=1`` and inserts nothing.
    """
    from scripts.import_packs import (
        UnsignedArchiveError,
        import_archive,
    )

    signature = request.headers.get("x-archive-signature", "").strip()
    if not signature:
        raise HTTPException(status_code=401, detail="missing X-Archive-Signature")

    secret = settings.PRECOMPUTE_HMAC_SECRET
    if not secret or len(secret) < 32:
        raise HTTPException(
            status_code=503, detail="PRECOMPUTE_HMAC_SECRET not configured",
        )

    archive_bytes = await request.body()
    if not archive_bytes:
        raise HTTPException(status_code=400, detail="empty archive body")

    try:
        result = await import_archive(
            db,
            archive_payload=archive_bytes,
            signature=signature,
            secret=secret,
            force_upgrade=force_upgrade,
        )
    except UnsignedArchiveError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    await audit.record_operator_action(
        db,
        actor_id=actor.actor_id,
        action="precompute.import_starter_packs",
        target_kind="archive",
        target_id="starter-pack-archive",
        after=dict(result),
    )
    await db.commit()
    return ImportPacksResult(**result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_job_view(row: PrecomputeJob) -> JobView:
    return JobView(
        id=row.id, topic_id=row.topic_id, status=row.status,
        attempt=int(row.attempt or 0), tier=row.tier,
        cost_cents=int(row.cost_cents or 0), error_text=row.error_text,
    )
