"""§21 Phase 3 — builder orchestration tests.

Covers:
  - AC-PRECOMP-BUILD-1 (state machine: queued → running → terminal)
  - AC-PRECOMP-BUILD-2 (tier escalation cheap → strong → strong+search)
  - AC-PRECOMP-BUILD-3 (atomic persist on success)
  - AC-PRECOMP-BUILD-5 (cost guard re-queues at the daily cap)
  - AC-PRECOMP-COST-6  (Tier-3 suppressed at ≥75% spend)
  - AC-PRECOMP-SAFETY-1 (banned topics short-circuit to rejected)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.db import PrecomputeJob, Topic
from app.services.precompute import builder, jobs
from app.services.precompute.evaluator import EvaluatorResult
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _result(score: int, *, tier="cheap", reasons=()) -> EvaluatorResult:
    return EvaluatorResult(score=score, tier=tier, blocking_reasons=tuple(reasons))


async def _seed(session, *, policy: str = "allowed") -> tuple[Topic, PrecomputeJob]:
    t = Topic(slug="t", display_name="T", policy_status=policy)
    session.add(t)
    await session.flush()
    j = await jobs.enqueue(session, topic_id=t.id)
    return t, j


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_banned_topic_short_circuits_to_rejected(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session, policy="banned")

    async def gen(topic, tier):
        raise AssertionError("generator must not run for banned topics")

    async def ev(*a, **k):
        raise AssertionError("evaluator must not run for banned topics")

    async def persist(*a, **k):
        raise AssertionError("persist must not run for banned topics")

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "rejected"
    assert "TOPIC_BANNED" in out.rejection_reasons
    assert (await sqlite_db_session.get(PrecomputeJob, j.id)).status == "rejected"


async def test_cheap_tier_pass_promotes_and_marks_succeeded(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session)
    persisted: list[object] = []

    async def gen(topic, tier):
        return ({"tier": tier}, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        return _result(8, tier=tier)

    async def persist(topic, artefact, result):
        persisted.append((topic.id, artefact, result.score))

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()

    assert out.status == "succeeded"
    assert out.final_tier == "cheap"
    assert out.score == 8
    assert len(persisted) == 1
    row = await sqlite_db_session.get(PrecomputeJob, j.id)
    assert row.status == "succeeded"
    assert row.tier == "cheap"
    assert row.cost_cents == 5


async def test_tier_escalates_when_cheap_fails(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session)

    seq = iter([_result(3), _result(4), _result(8, tier="strong+search")])

    async def gen(topic, tier):
        return ({"tier": tier}, 10)

    async def ev(artefact, tier, pass_score, two_judge):
        return next(seq)

    async def persist(topic, artefact, result):
        return None

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "succeeded"
    assert out.final_tier == "strong+search"
    row = await sqlite_db_session.get(PrecomputeJob, j.id)
    assert row.cost_cents == 30  # 3 attempts × 10c


async def test_persist_failure_marks_failed(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session)

    async def gen(topic, tier):
        return ({}, 1)

    async def ev(artefact, tier, pass_score, two_judge):
        return _result(9)

    async def persist(*a, **k):
        raise RuntimeError("disk on fire")

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "failed"
    assert "persist_failed" in out.rejection_reasons


async def test_daily_budget_cap_requeues_with_delay(sqlite_db_session) -> None:
    # Pre-spend the entire budget via an existing job row.
    t, j = await _seed(sqlite_db_session)
    sqlite_db_session.add(PrecomputeJob(
        topic_id=t.id, status="succeeded", attempt=1, cost_cents=100,
    ))
    await sqlite_db_session.commit()

    async def gen(*a, **k):
        raise AssertionError("must not run when budget exceeded")

    async def ev(*a, **k):
        raise AssertionError("must not run")

    async def persist(*a, **k):
        raise AssertionError("must not run")

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=1.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "delayed"
    row = (await sqlite_db_session.execute(
        select(PrecomputeJob).where(PrecomputeJob.id == j.id)
    )).scalar_one()
    assert row.status == "queued"
    assert row.delayed_until is not None


async def test_tier3_suppressed_when_75pct_spent(sqlite_db_session) -> None:
    # Spend 75 cents of $1 budget so tier-3 cutoff blocks escalation.
    t, j = await _seed(sqlite_db_session)
    sqlite_db_session.add(PrecomputeJob(
        topic_id=t.id, status="succeeded", attempt=1, cost_cents=75,
    ))
    await sqlite_db_session.commit()

    seq = iter([_result(3), _result(4)])  # cheap fails, strong fails

    async def gen(topic, tier):
        # Tier-3 must never be requested.
        assert tier != "strong+search"
        return ({"tier": tier}, 1)

    async def ev(artefact, tier, pass_score, two_judge):
        return next(seq)

    async def persist(*a, **k):
        return None

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=1.0, tier3_budget_pct=0.75, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "rejected"
    assert "TIER3_BUDGET_EXCEEDED" in out.rejection_reasons
