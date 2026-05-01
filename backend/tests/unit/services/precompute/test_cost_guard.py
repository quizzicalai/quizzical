"""§21 Phase 3 — daily-budget cost guard tests.

Covers:
  - AC-PRECOMP-BUILD-5  (`can_attempt` flips at the daily cap)
  - AC-PRECOMP-COST-6   (`can_use_tier3` flips at 75% of daily cap)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.db import PrecomputeJob, Topic
from app.services.precompute.cost_guard import next_attempt_delay, snapshot, today_spend_cents
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


async def _seed_jobs(session, *, today_total_cents: int, yesterday_cents: int = 0) -> Topic:
    topic = Topic(slug="t", display_name="T", policy_status="allowed")
    session.add(topic)
    await session.flush()

    if today_total_cents:
        session.add(PrecomputeJob(
            topic_id=topic.id, status="succeeded", attempt=1,
            cost_cents=today_total_cents,
        ))
    if yesterday_cents:
        old = PrecomputeJob(
            topic_id=topic.id, status="succeeded", attempt=1,
            cost_cents=yesterday_cents,
        )
        session.add(old)
        await session.flush()
        # Backdate after insert (server_default already populated created_at).
        old.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    await session.commit()
    return topic


async def test_today_spend_excludes_prior_days(sqlite_db_session) -> None:
    await _seed_jobs(sqlite_db_session, today_total_cents=100, yesterday_cents=999)
    spent = await today_spend_cents(sqlite_db_session)
    assert spent == 100


async def test_can_attempt_flips_at_daily_cap(sqlite_db_session) -> None:
    # Cap = $1.00 → 100 cents. Spend = 100 cents → can_attempt False.
    await _seed_jobs(sqlite_db_session, today_total_cents=100)
    snap = await snapshot(sqlite_db_session, daily_budget_usd=1.0)
    assert snap.spent_cents == 100
    assert snap.daily_cap_cents == 100
    assert snap.can_attempt() is False
    assert snap.remaining_cents == 0


async def test_can_use_tier3_flips_at_75pct(sqlite_db_session) -> None:
    # Cap = $1.00, Tier-3 cutoff = 75c. Spend 75c → tier3 disabled, attempts ok.
    await _seed_jobs(sqlite_db_session, today_total_cents=75)
    snap = await snapshot(
        sqlite_db_session, daily_budget_usd=1.0, tier3_budget_pct=0.75,
    )
    assert snap.tier3_cap_cents == 75
    assert snap.can_use_tier3() is False
    assert snap.can_attempt() is True


async def test_tier3_pct_clamped_to_unit_interval(sqlite_db_session) -> None:
    await _seed_jobs(sqlite_db_session, today_total_cents=0)
    high = await snapshot(sqlite_db_session, daily_budget_usd=1.0, tier3_budget_pct=10.0)
    low = await snapshot(sqlite_db_session, daily_budget_usd=1.0, tier3_budget_pct=-2.0)
    assert high.tier3_cap_cents == 100
    assert low.tier3_cap_cents == 0


def test_next_attempt_delay_is_next_utc_midnight() -> None:
    now = datetime(2026, 4, 30, 13, 30, tzinfo=timezone.utc)
    nxt = next_attempt_delay(now)
    assert nxt == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
