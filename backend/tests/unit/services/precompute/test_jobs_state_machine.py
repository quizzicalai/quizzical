"""§21 Phase 3 — `precompute_jobs` state-machine tests (`AC-PRECOMP-BUILD-1`)."""

from __future__ import annotations

import pytest

from app.models.db import Topic
from app.services.precompute.jobs import (
    IllegalJobTransition,
    JobStatus,
    assert_legal_transition,
    enqueue,
    transition,
)
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "src,dst",
    [
        ("queued", "running"),
        ("running", "succeeded"),
        ("running", "failed"),
        ("running", "rejected"),
        ("running", "running"),  # tier escalation
        ("running", "queued"),  # budget-delay re-queue
        ("queued", "rejected"),  # banned topic short-circuit
        ("failed", "queued"),  # operator retry
        ("queued", "queued"),  # delay
    ],
)
def test_legal_transitions_pass(src: str, dst: str) -> None:
    assert_legal_transition(src, dst)


@pytest.mark.parametrize(
    "src,dst",
    [
        ("queued", "succeeded"),
        ("succeeded", "running"),
        ("rejected", "queued"),
        ("garbage", "running"),
        ("running", "garbage"),
    ],
)
def test_illegal_transitions_raise(src: str, dst: str) -> None:
    with pytest.raises(IllegalJobTransition):
        assert_legal_transition(src, dst)


async def _topic(session) -> Topic:
    t = Topic(slug="t", display_name="T", policy_status="allowed")
    session.add(t)
    await session.flush()
    return t


async def test_enqueue_creates_queued_row(sqlite_db_session) -> None:
    t = await _topic(sqlite_db_session)
    job = await enqueue(sqlite_db_session, topic_id=t.id)
    await sqlite_db_session.commit()
    assert job.status == JobStatus.QUEUED.value
    assert job.attempt == 0
    assert job.cost_cents == 0


async def test_transition_updates_columns_and_increments_attempt(sqlite_db_session) -> None:
    t = await _topic(sqlite_db_session)
    job = await enqueue(sqlite_db_session, topic_id=t.id)

    job = await transition(
        sqlite_db_session, job, to=JobStatus.RUNNING, tier="cheap", cost_cents=12,
    )
    assert job.status == "running"
    assert job.tier == "cheap"
    assert job.attempt == 1
    assert job.cost_cents == 12

    job = await transition(
        sqlite_db_session, job, to=JobStatus.RUNNING, tier="strong", cost_cents=8,
    )
    assert job.attempt == 2
    assert job.cost_cents == 20

    job = await transition(sqlite_db_session, job, to=JobStatus.SUCCEEDED)
    assert job.status == "succeeded"
    await sqlite_db_session.commit()


async def test_transition_rejects_illegal_move(sqlite_db_session) -> None:
    t = await _topic(sqlite_db_session)
    job = await enqueue(sqlite_db_session, topic_id=t.id)
    with pytest.raises(IllegalJobTransition):
        await transition(sqlite_db_session, job, to=JobStatus.SUCCEEDED)
