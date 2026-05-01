"""§21 Phase 3 — `precompute_jobs` state machine.

The job ledger is the single source of truth for what the build worker is
or has been doing on a topic. The state machine has four legal terminals
and one in-flight state:

    queued → running → (succeeded | failed | rejected)

Plus an optional re-queue:

    failed → queued      (operator retry)
    queued → queued      (delay) — `delayed_until` advanced.

Any other transition raises `IllegalJobTransition` so the worker never
silently corrupts the ledger. `AC-PRECOMP-BUILD-1`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import PrecomputeJob


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


_LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    # Banned topic short-circuit (`AC-PRECOMP-SAFETY-1`) → queued→rejected legal.
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.REJECTED}
    ),
    # Tier escalation (`AC-PRECOMP-BUILD-2`) re-enters RUNNING for each
    # attempt and bumps `attempt` — running→running is a legal self-loop.
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.REJECTED,
            JobStatus.RUNNING,
            JobStatus.QUEUED,  # budget delay re-queues
        }
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),  # operator retry
    JobStatus.REJECTED: frozenset(),
}


class IllegalJobTransition(Exception):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"illegal precompute_jobs transition: {from_status!r} -> {to_status!r}"
        )
        self.from_status = from_status
        self.to_status = to_status


def assert_legal_transition(from_status: str, to_status: str) -> None:
    """Pure helper kept separate from the persistence path so it can be
    exercised by a fast unit test without a live DB session."""
    try:
        f = JobStatus(from_status)
        t = JobStatus(to_status)
    except ValueError as exc:
        raise IllegalJobTransition(from_status, to_status) from exc
    if t not in _LEGAL_TRANSITIONS[f]:
        raise IllegalJobTransition(from_status, to_status)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def enqueue(
    db: AsyncSession,
    *,
    topic_id: UUID,
    delayed_until: datetime | None = None,
) -> PrecomputeJob:
    """Create a fresh `queued` row. Caller owns the commit."""
    row = PrecomputeJob(
        topic_id=topic_id,
        status=JobStatus.QUEUED.value,
        attempt=0,
        cost_cents=0,
        delayed_until=delayed_until,
    )
    db.add(row)
    await db.flush()
    return row


async def transition(
    db: AsyncSession,
    job: PrecomputeJob,
    *,
    to: JobStatus | str,
    cost_cents: int | None = None,
    tier: str | None = None,
    error_text: str | None = None,
    delayed_until: datetime | None = None,
    evaluator_history: dict[str, Any] | None = None,
) -> PrecomputeJob:
    """Move `job` to `to`, validating the transition and updating ledger
    columns in one place. Caller owns the commit.

    `cost_cents` is *added* to the existing total — each tier attempt
    accumulates spend independently of the terminal status (`AC-PRECOMP-BUILD-5`).
    """
    target = to.value if isinstance(to, JobStatus) else str(to)
    assert_legal_transition(job.status, target)

    job.status = target
    if tier is not None:
        job.tier = tier
    if cost_cents:
        job.cost_cents = int(job.cost_cents or 0) + int(cost_cents)
    if error_text is not None:
        job.error_text = str(error_text)[:2000]
    if delayed_until is not None:
        job.delayed_until = delayed_until
    if evaluator_history is not None:
        job.evaluator_history = evaluator_history
    if target == JobStatus.RUNNING.value:
        job.attempt = int(job.attempt or 0) + 1
    job.last_updated_at = datetime.now(timezone.utc)
    await db.flush()
    return job
