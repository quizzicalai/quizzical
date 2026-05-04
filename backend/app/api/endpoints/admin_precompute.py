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
# Promotion candidates (nightly user-quiz → starter-pack pipeline)
# ---------------------------------------------------------------------------


class PromotionCandidate(BaseModel):
    """One completed user quiz session, packaged so it can be evaluated
    and promoted into a published topic pack by the offline nightly job.

    The shape intentionally mirrors the `topics[]` entries consumed by
    `scripts.build_starter_packs.build_archive`:

      - ``slug`` / ``display_name``    — derived from the user-provided category
      - ``synopsis``                    — `session_history.category_synopsis`
      - ``characters``                  — `session_history.character_set` snapshot
      - ``baseline_questions``          — `session_questions.baseline_questions`
      - ``final_result``                — `session_history.final_result` (read-only)

    The endpoint never returns sessions that already correspond to a topic
    with `current_pack_id` set (would be a wasted promotion attempt) and
    never returns rows whose user feedback was negative.
    """

    session_id: UUID
    category: str
    completed_at: str
    slug: str
    display_name: str
    synopsis: dict[str, Any]
    characters: list[dict[str, Any]]
    baseline_questions: list[dict[str, Any]]
    final_result: dict[str, Any] | None = None
    judge_plan_score: int | None = None
    user_sentiment: str | None = None


class PromotionCandidatesResponse(BaseModel):
    candidates: list[PromotionCandidate]
    total: int
    since_hours: int


@router.get(
    "/promotion-candidates",
    response_model=PromotionCandidatesResponse,
    summary="List completed user quizzes eligible for nightly promotion to a starter pack.",
)
async def list_promotion_candidates(  # noqa: C901  (linear filter pipeline with several gating guards)
    db: Annotated[AsyncSession, Depends(get_db_session)],
    _: Annotated[OperatorPrincipal, Depends(require_operator)],
    since_hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=50, ge=1, le=500),
    min_judge_score: int = Query(default=7, ge=1, le=10),
    require_baseline_questions: bool = Query(default=True),
) -> PromotionCandidatesResponse:
    """Return completed sessions worth promoting.

    Filtering rules:

      - ``is_completed = TRUE``
      - ``completed_at >= now - since_hours``
      - ``judge_plan_score IS NULL OR >= min_judge_score`` (NULL allowed
        so legacy completions that pre-date the judge are still eligible)
      - ``user_sentiment != 'NEGATIVE'`` (positive or absent feedback OK)
      - When ``require_baseline_questions = True``: a session_questions
        row exists with at least one baseline question
      - The category does not already resolve to a topic with
        ``current_pack_id`` set (would already be pre-populated content)
    """
    from datetime import datetime, timedelta, timezone

    from app.models.db import (
        SessionHistory,
        SessionQuestions,
        UserSentimentEnum,
    )
    from app.services.precompute.lookup import _slugify

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    q = (
        select(SessionHistory)
        .where(SessionHistory.is_completed.is_(True))
        .where(SessionHistory.completed_at.is_not(None))
        .where(SessionHistory.completed_at >= cutoff)
        .order_by(SessionHistory.completed_at.desc())
        .limit(limit * 4)  # fetch headroom; we filter again in Python
    )
    rows = (await db.execute(q)).scalars().all()

    # Pre-load all topics that already have a current_pack_id — we reject
    # candidates whose slug collides with one of these to avoid wasted work.
    pre_packed_slugs: set[str] = set(
        (
            await db.execute(
                select(Topic.slug).where(Topic.current_pack_id.is_not(None))
            )
        ).scalars().all()
    )

    def _passes_static_gates(row: SessionHistory) -> bool:
        if row.judge_plan_score is not None and row.judge_plan_score < min_judge_score:
            return False
        if row.user_sentiment == UserSentimentEnum.NEGATIVE:
            return False
        if not row.final_result:
            return False
        if not isinstance(row.category_synopsis, dict) or not row.category_synopsis:
            return False
        char_set = row.character_set or []
        return isinstance(char_set, list) and bool(char_set)

    async def _load_baseline(session_id: Any) -> list[dict[str, Any]]:
        sq = (
            await db.execute(
                select(SessionQuestions).where(
                    SessionQuestions.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        raw = (sq.baseline_questions if sq else None) or {}
        # baseline_questions JSON is either {"questions": [...]} or bare list.
        if isinstance(raw, dict):
            return list(raw.get("questions") or [])
        if isinstance(raw, list):
            return list(raw)
        return []

    def _build(row: SessionHistory, slug: str, baseline: list[dict[str, Any]]) -> PromotionCandidate:
        sentiment = row.user_sentiment
        sentiment_value = sentiment.value if hasattr(sentiment, "value") else sentiment
        return PromotionCandidate(
            session_id=row.session_id,
            category=row.category,
            completed_at=row.completed_at.isoformat() if row.completed_at else "",
            slug=slug,
            display_name=row.category.strip(),
            synopsis=row.category_synopsis,
            characters=list(row.character_set or []),
            baseline_questions=baseline,
            final_result=row.final_result,
            judge_plan_score=row.judge_plan_score,
            user_sentiment=sentiment_value,
        )

    candidates: list[PromotionCandidate] = []
    for row in rows:
        if len(candidates) >= limit:
            break
        if not _passes_static_gates(row):
            continue
        slug = _slugify(row.category)
        if not slug or slug in pre_packed_slugs:
            continue
        baseline: list[dict[str, Any]] = []
        if require_baseline_questions:
            baseline = await _load_baseline(row.session_id)
            if not baseline:
                continue
        candidates.append(_build(row, slug, baseline))

    return PromotionCandidatesResponse(
        candidates=candidates,
        total=len(candidates),
        since_hours=since_hours,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_job_view(row: PrecomputeJob) -> JobView:
    return JobView(
        id=row.id, topic_id=row.topic_id, status=row.status,
        attempt=int(row.attempt or 0), tier=row.tier,
        cost_cents=int(row.cost_cents or 0), error_text=row.error_text,
    )
