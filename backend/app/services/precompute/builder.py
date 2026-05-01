"""§21 Phase 3 — build worker orchestration (`AC-PRECOMP-BUILD-1..5`).

The orchestrator is intentionally generator-agnostic: a `GenerateFn` produces
the artefact for a given tier, an `EvaluateFn` scores it (delegating to
`app.services.precompute.evaluator`), and a `PersistFn` writes the accepted
artefacts atomically. This separation lets Phase 3 cover the full state
machine + cost / safety integration without hard-coding the LLM / DB
plumbing that lands in Phases 4–7.

Tier escalation order (`AC-PRECOMP-BUILD-2`):

    cheap → strong → strong+search

with each tier independently:
- cost-guarded (`AC-PRECOMP-BUILD-5`),
- safety-gated (`AC-PRECOMP-SAFETY-1,2`),
- bounded by `max_build_attempts`,
- and rolled back atomically on failure (`AC-PRECOMP-BUILD-3`).

Successful build promotes the new pack version (`AC-PRECOMP-PROMOTE-1..4`)
which in Phase 3 we model as a `PersistFn` callback that the caller wires
to whatever atomic-swap routine is appropriate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import PrecomputeJob, Topic
from app.services.precompute import cost_guard, jobs, safety
from app.services.precompute.evaluator import (
    EscalateToTier3,
    EvaluatorResult,
    JudgeTier,
    passes,
)

logger = structlog.get_logger("app.services.precompute.builder")

TIER_ORDER: tuple[JudgeTier, ...] = ("cheap", "strong", "strong+search")


@dataclass(frozen=True)
class BuildOutcome:
    job_id: UUID
    status: str  # "succeeded" | "failed" | "rejected" | "delayed"
    final_tier: JudgeTier | None
    score: int | None
    rejection_reasons: tuple[str, ...]


GenerateFn = Callable[[Topic, JudgeTier], Awaitable[tuple[object, int]]]
"""Returns `(artefact, cost_cents_for_this_attempt)`."""

EvaluateFn = Callable[[object, JudgeTier, int, bool], Awaitable[EvaluatorResult]]
"""Args: `(artefact, tier, pass_score, require_two_judge)`."""

PersistFn = Callable[[Topic, object, EvaluatorResult], Awaitable[None]]
"""Atomic writer: insert pack rows + topic.current_pack_id under one transaction."""


def _next_tier(current: JudgeTier | None, *, force_min: JudgeTier | None) -> JudgeTier | None:
    """Return the next tier strictly above `current`. Honours `force_min`
    by skipping anything cheaper than the minimum the policy demands."""
    order = list(TIER_ORDER)
    floor = order.index(force_min) if force_min in order else 0
    if current is None:
        return order[floor]
    try:
        idx = order.index(current)
    except ValueError:
        return order[floor]
    nxt = idx + 1
    if nxt >= len(order):
        return None
    return order[max(nxt, floor)]


async def run_build(  # noqa: C901 — orchestrator: branching is inherent to the state machine
    db: AsyncSession,
    *,
    topic: Topic,
    job: PrecomputeJob,
    generate_fn: GenerateFn,
    evaluate_fn: EvaluateFn,
    persist_fn: PersistFn,
    daily_budget_usd: float,
    tier3_budget_pct: float = 0.75,
    default_pass_score: int,
    restricted_pass_score: int = 9,
    max_attempts: int = 3,
) -> BuildOutcome:
    """Drive `job` through the tier escalation state machine.

    The function NEVER calls `db.commit()` — that is the caller's job at
    the end of the request / worker loop. We do call `db.flush()` on
    every transition so subsequent `cost_guard.today_spend_cents` queries
    see the running totals.
    """

    # Hard policy gate first (`AC-PRECOMP-SAFETY-1`).
    try:
        safety.assert_topic_can_be_enqueued(
            policy_status=topic.policy_status, topic_id=str(topic.id),
            slug=getattr(topic, "slug", None),
        )
    except safety.TopicBannedError as exc:
        await jobs.transition(
            db, job, to=jobs.JobStatus.REJECTED,
            error_text=f"{exc.code}: {exc}",
        )
        logger.info("precompute.build.banned", topic_id=str(topic.id), code=exc.code)
        return BuildOutcome(job.id, "rejected", None, None, (exc.code,))

    constraints = safety.evaluator_constraints_for(
        policy_status=topic.policy_status,
        default_pass_score=default_pass_score,
        restricted_pass_score=restricted_pass_score,
    )
    pass_score = constraints.pass_score or default_pass_score

    current_tier: JudgeTier | None = None
    final_result: EvaluatorResult | None = None
    rejection_reasons: list[str] = []

    for attempt_idx in range(max_attempts):
        next_tier = _next_tier(current_tier, force_min=constraints.force_tier)
        if next_tier is None:
            break

        # Cost guard before EACH attempt (`AC-PRECOMP-BUILD-5`).
        snap = await cost_guard.snapshot(
            db,
            daily_budget_usd=daily_budget_usd,
            tier3_budget_pct=tier3_budget_pct,
        )
        if not snap.can_attempt():
            await jobs.transition(
                db, job, to=jobs.JobStatus.QUEUED,
                delayed_until=cost_guard.next_attempt_delay(),
                error_text="DAILY_BUDGET_EXCEEDED",
            )
            logger.warning(
                "precompute.build.budget_exceeded",
                topic_id=str(topic.id),
                spent_cents=snap.spent_cents,
                cap_cents=snap.daily_cap_cents,
            )
            return BuildOutcome(job.id, "delayed", current_tier, None, ("DAILY_BUDGET_EXCEEDED",))
        if next_tier == "strong+search" and not snap.can_use_tier3():
            # Tier-3 not affordable; nothing cheaper to try → reject.
            rejection_reasons.append("TIER3_BUDGET_EXCEEDED")
            await jobs.transition(
                db, job, to=jobs.JobStatus.REJECTED,
                error_text="TIER3_BUDGET_EXCEEDED",
            )
            logger.info(
                "precompute.build.tier3_blocked",
                topic_id=str(topic.id),
                spent_cents=snap.spent_cents,
                tier3_cap_cents=snap.tier3_cap_cents,
            )
            return BuildOutcome(job.id, "rejected", current_tier, None, tuple(rejection_reasons))

        await jobs.transition(db, job, to=jobs.JobStatus.RUNNING, tier=next_tier)
        current_tier = next_tier
        try:
            artefact, cost_cents = await generate_fn(topic, current_tier)
        except Exception as exc:  # noqa: BLE001 — generator failures are logged + re-queued
            logger.exception("precompute.build.generate_failed", topic_id=str(topic.id), tier=current_tier)
            await jobs.transition(
                db, job, to=jobs.JobStatus.FAILED,
                error_text=f"generate_failed: {exc!s}"[:500],
            )
            return BuildOutcome(job.id, "failed", current_tier, None, ("generate_failed",))

        # Charge the attempt regardless of judge outcome.
        if cost_cents:
            job.cost_cents = int(job.cost_cents or 0) + int(cost_cents)
            await db.flush()

        try:
            result = await evaluate_fn(
                artefact, current_tier, pass_score, constraints.require_two_judge
            )
        except EscalateToTier3 as esc:
            logger.info(
                "precompute.build.escalate_tier3",
                topic_id=str(topic.id), scores=esc.scores,
            )
            continue  # next loop iteration picks the next tier

        final_result = result
        if passes(result, pass_score=pass_score):
            try:
                await persist_fn(topic, artefact, result)
            except Exception as exc:  # noqa: BLE001 — atomic persist failure
                logger.exception("precompute.build.persist_failed", topic_id=str(topic.id))
                await jobs.transition(
                    db, job, to=jobs.JobStatus.FAILED,
                    error_text=f"persist_failed: {exc!s}"[:500],
                )
                return BuildOutcome(job.id, "failed", current_tier, result.score, ("persist_failed",))
            await jobs.transition(db, job, to=jobs.JobStatus.SUCCEEDED)
            logger.info(
                "precompute.build.succeeded",
                topic_id=str(topic.id), tier=current_tier,
                score=result.score, attempt=attempt_idx + 1,
            )
            return BuildOutcome(job.id, "succeeded", current_tier, result.score, ())

        rejection_reasons.extend(result.blocking_reasons or ("score_below_threshold",))
        logger.info(
            "precompute.build.attempt_rejected",
            topic_id=str(topic.id), tier=current_tier,
            score=result.score, reasons=list(result.blocking_reasons),
        )

    # Exhausted all attempts / tiers without a passing result.
    await jobs.transition(
        db, job, to=jobs.JobStatus.REJECTED,
        error_text="exhausted_tiers",
    )
    return BuildOutcome(
        job.id, "rejected", current_tier,
        final_result.score if final_result else None,
        tuple(rejection_reasons) or ("exhausted_tiers",),
    )
