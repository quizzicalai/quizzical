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
