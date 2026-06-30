"""QuizJobRepository — durable live-agent job tracking + crash-recovery claim.

The atomic ``claim_stale`` is the heart of multi-replica-safe recovery, so the
selection + heartbeat-bump + attempt-cap logic is pinned here against the real
(sqlite) DB.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.db import QuizJob
from app.services.database import QuizJobRepository


async def _age_heartbeat(session, quiz_id, seconds):
    job = await session.get(QuizJob, quiz_id)
    job.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    await session.commit()


@pytest.mark.anyio
async def test_mark_running_increments_attempts_then_succeeds(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()

    await repo.mark_running(qid)
    await sqlite_db_session.commit()
    job = await sqlite_db_session.get(QuizJob, qid)
    assert job.status == "running"
    assert job.attempts == 1

    await repo.mark_running(qid)  # a re-run (recovery) increments
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(job)
    assert job.attempts == 2

    await repo.mark_succeeded(qid)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(job)
    assert job.status == "succeeded"


@pytest.mark.anyio
async def test_claim_stale_selects_only_stale_running_and_bumps_heartbeat(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    fresh, stale = uuid.uuid4(), uuid.uuid4()
    await repo.mark_running(fresh)
    await repo.mark_running(stale)
    await sqlite_db_session.commit()
    await _age_heartbeat(sqlite_db_session, stale, 999)

    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == [stale]  # only the stale one

    # Claiming bumped the heartbeat -> an immediate re-claim finds nothing
    # (prevents a concurrent replica / next cycle re-running the same job).
    again = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert again == []


@pytest.mark.anyio
async def test_claim_respects_attempt_cap_then_fail_exhausted(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    for _ in range(3):  # attempts == 3 == max
        await repo.mark_running(qid)
    await sqlite_db_session.commit()
    await _age_heartbeat(sqlite_db_session, qid, 999)

    # At the attempt cap -> not claimed for another run.
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == []

    # ...but fail_exhausted marks it terminally failed.
    failed = await repo.fail_exhausted(stale_after_s=180, max_attempts=3)
    await sqlite_db_session.commit()
    assert failed == [qid]
    job = await sqlite_db_session.get(QuizJob, qid)
    await sqlite_db_session.refresh(job)
    assert job.status == "failed"


@pytest.mark.anyio
async def test_succeeded_job_is_never_claimed(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    await repo.mark_running(qid)
    await repo.mark_succeeded(qid)
    await sqlite_db_session.commit()
    await _age_heartbeat(sqlite_db_session, qid, 999)  # old, but succeeded
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == []


# --------------------------------------------------------------------------
# Audit P1 — heartbeat keeps a slow-but-ALIVE run unclaimed
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_heartbeat_keeps_alive_run_unclaimed(sqlite_db_session):
    """A long run past stale_after_s that keeps emitting heartbeats must NOT be
    misclassified stale and re-claimed (the core double-spend hole)."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    await repo.mark_running(qid)
    await sqlite_db_session.commit()

    # Simulate a run that started > stale_after_s ago but is still alive.
    await _age_heartbeat(sqlite_db_session, qid, 999)
    # Without a heartbeat it WOULD be claimed -> prove that first.
    would_claim = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    assert would_claim == [qid]

    # Now age it again, but THIS time the alive run emits a fresh heartbeat
    # before the next sweep. The fresh heartbeat resets the staleness clock.
    await _age_heartbeat(sqlite_db_session, qid, 999)
    await repo.heartbeat(qid)
    await sqlite_db_session.commit()
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == []  # alive run is left to finish, no concurrent re-run


@pytest.mark.anyio
async def test_heartbeat_only_touches_running_rows(sqlite_db_session):
    """heartbeat() is a no-op on a terminal row (so a late heartbeat from a
    cancelled loop can never resurrect a succeeded/failed job)."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    await repo.mark_running(qid)
    await repo.mark_succeeded(qid)
    await sqlite_db_session.commit()
    before = (await sqlite_db_session.get(QuizJob, qid)).last_heartbeat_at
    await repo.heartbeat(qid)  # status != running -> WHERE matches nothing
    await sqlite_db_session.commit()
    job = await sqlite_db_session.get(QuizJob, qid)
    await sqlite_db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.last_heartbeat_at == before


# --------------------------------------------------------------------------
# Audit P1 — claim_stale grants a single stale row EXACTLY once
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_concurrent_claim_grants_exactly_once(sqlite_db_session):
    """Two sweepers (two replicas) firing claim_stale against the SAME stale row
    must grant it to exactly one. On sqlite the atomic single-statement claim
    bumps the heartbeat, so the second claimer's re-asserted staleness predicate
    no longer matches -> zero double-grants."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    await repo.mark_running(qid)
    await sqlite_db_session.commit()
    await _age_heartbeat(sqlite_db_session, qid, 999)

    first = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    second = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()

    assert first == [qid]
    assert second == []  # the second sweeper gets nothing
    # The row is granted exactly once across both claimers.
    assert (first + second).count(qid) == 1


@pytest.mark.anyio
async def test_mark_running_reset_attempts_keeps_recovery_budget_per_run(sqlite_db_session):
    """The recovery budget (attempts) is per-RUN, not per-quiz-step. The handler
    creates the row with reset_attempts=True before each scheduled run, so prior
    /proceed + /next steps don't prematurely exhaust max_attempts for a crash on
    a later question."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()

    # Simulate several quiz steps (proceed, next, next...) each scheduling a run:
    # handler resets, then the bg task's mark_running bumps to 1.
    for _ in range(5):
        await repo.mark_running(qid, reset_attempts=True)  # handler, pre-schedule
        await repo.mark_running(qid)                        # bg task's own mark
    await sqlite_db_session.commit()

    job = await sqlite_db_session.get(QuizJob, qid)
    assert job.attempts == 1, "each fresh run resets the recovery budget to 1"

    # A crash on this late step leaves it stale+running with attempts=1 -> still
    # claimable for recovery (would NOT be if attempts had accumulated to 5+).
    await _age_heartbeat(sqlite_db_session, qid, 999)
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == [qid]


@pytest.mark.anyio
async def test_get_status_reports_job_lifecycle(sqlite_db_session):
    """get_status surfaces the durable status used by /status to fail-fast a
    deterministically-failed run (and None when no row exists)."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    assert await repo.get_status(qid) is None  # no row yet

    await repo.mark_running(qid)
    await sqlite_db_session.commit()
    assert await repo.get_status(qid) == "running"

    await repo.mark_failed(qid, "boom")
    await sqlite_db_session.commit()
    assert await repo.get_status(qid) == "failed"


# --------------------------------------------------------------------------
# Hitlist #8 — mark_retryable hands a transient failure back to the sweeper
# (status stays 'running', heartbeat staled) without touching attempts;
# get_attempts surfaces the recovery counter for the transient gate.
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_mark_retryable_keeps_running_and_is_immediately_claimable(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    await repo.mark_running(qid)  # attempts=1, fresh heartbeat
    await sqlite_db_session.commit()

    # A fresh 'running' row is NOT claimable (heartbeat is current).
    not_yet = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert not_yet == []

    await repo.mark_retryable(qid, "transient 503")
    await sqlite_db_session.commit()
    job = await sqlite_db_session.get(QuizJob, qid)
    await sqlite_db_session.refresh(job)
    # Status stays 'running' (not a terminal 'failed'); attempts untouched.
    assert job.status == "running"
    assert job.attempts == 1
    assert job.last_heartbeat_at.year == 1970  # staled to the epoch
    assert "transient 503" in (job.last_error or "")

    # Now the next sweep claims it immediately (no waiting stale_after_s).
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == [qid]


@pytest.mark.anyio
async def test_mark_retryable_still_bounded_by_fail_exhausted(sqlite_db_session):
    """A transient failure handed back via mark_retryable does not bypass the
    attempt cap: once attempts hit max, fail_exhausted marks it failed (so a
    persistently-flaky run can't loop forever)."""
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    for _ in range(3):  # attempts == 3 == max
        await repo.mark_running(qid)
    await repo.mark_retryable(qid, "still down")
    await sqlite_db_session.commit()

    # At the cap -> not re-claimed, and fail_exhausted terminates it.
    claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
    await sqlite_db_session.commit()
    assert claimed == []
    failed = await repo.fail_exhausted(stale_after_s=180, max_attempts=3)
    await sqlite_db_session.commit()
    assert failed == [qid]


@pytest.mark.anyio
async def test_get_attempts_reports_counter(sqlite_db_session):
    repo = QuizJobRepository(sqlite_db_session)
    qid = uuid.uuid4()
    assert await repo.get_attempts(qid) is None  # no row
    await repo.mark_running(qid)
    await sqlite_db_session.commit()
    assert await repo.get_attempts(qid) == 1
    await repo.mark_running(qid)
    await sqlite_db_session.commit()
    assert await repo.get_attempts(qid) == 2
