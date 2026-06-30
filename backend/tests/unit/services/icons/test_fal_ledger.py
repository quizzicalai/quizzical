"""FAL spend ledger + hard lifetime $-cap tests (PRIORITY 1 — the cost guard).

The invariant under test: no FAL generation proceeds without a pre-flight cap
check + a post-call ledger record, and the cap is enforced off the PERSISTENT
DB spend (so it holds across builds/processes).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import FalSpendLedger
from app.services.icons.fal_ledger import FalLedger
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


class _Budget:
    """Stand-in for settings.images.fal_budget."""

    def __init__(self, cap_usd=150.0, cost_per_image_usd=0.011, enforce=True):
        self.cap_usd = cap_usd
        self.cost_per_image_usd = cost_per_image_usd
        self.enforce = enforce

    @property
    def cap_cents(self):
        return int(round(self.cap_usd * 100))

    @property
    def cost_per_image_cents(self):
        return self.cost_per_image_usd * 100.0


async def _ledger_rows(session: AsyncSession):
    return (await session.execute(select(FalSpendLedger))).scalars().all()


async def _ledger_sum(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(FalSpendLedger.cost_cents), 0))
            )
        ).scalar_one()
        or 0
    )


# ---------------------------------------------------------------------------
# Records every charge; sum is persistent
# ---------------------------------------------------------------------------

async def test_guarded_generate_records_charge(sqlite_db_session: AsyncSession):
    ledger = FalLedger(sqlite_db_session, config=_Budget())
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return "https://fal.media/abc.png"

    url = await ledger.guarded_generate(_gen, purpose="qa_image", topic_slug="harry-potter")
    assert url == "https://fal.media/abc.png"
    assert calls["n"] == 1

    rows = await _ledger_rows(sqlite_db_session)
    assert len(rows) == 1
    assert rows[0].status == "charged"
    assert rows[0].cost_cents == 1  # round(1.1) == 1 cent
    assert rows[0].purpose == "qa_image"
    assert rows[0].topic_slug == "harry-potter"
    assert rows[0].fal_request_url == "https://fal.media/abc.png"


async def test_failed_fal_call_still_charges(sqlite_db_session: AsyncSession):
    """FAL bills the moment the call is accepted, so a None result still charges."""
    ledger = FalLedger(sqlite_db_session, config=_Budget())

    async def _gen():
        return None  # fail-open (timeout / NSFW / no url)

    url = await ledger.guarded_generate(_gen)
    assert url is None
    rows = await _ledger_rows(sqlite_db_session)
    assert len(rows) == 1
    assert rows[0].status == "charged"
    assert rows[0].cost_cents == 1


# ---------------------------------------------------------------------------
# Hard cap enforcement
# ---------------------------------------------------------------------------

async def test_cap_blocks_when_exhausted(sqlite_db_session: AsyncSession):
    # Cap = 1 cent; cost per image = 1 cent. First call fits; second is blocked.
    cfg = _Budget(cap_usd=0.01, cost_per_image_usd=0.01, enforce=True)
    ledger = FalLedger(sqlite_db_session, config=cfg)
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return f"https://fal.media/{calls['n']}.png"

    first = await ledger.guarded_generate(_gen)
    second = await ledger.guarded_generate(_gen)

    assert first is not None
    assert second is None  # cap refused it
    assert calls["n"] == 1  # FAL was NOT called the second time

    rows = await _ledger_rows(sqlite_db_session)
    # One 'charged' (1 cent) + one 'blocked' (0 cent) audit row.
    statuses = sorted(r.status for r in rows)
    assert statuses == ["blocked", "charged"]
    assert await _ledger_sum(sqlite_db_session) == 1  # never exceeds the cap


async def test_enforce_false_records_but_never_blocks(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.01, cost_per_image_usd=0.01, enforce=False)
    ledger = FalLedger(sqlite_db_session, config=cfg)
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return "https://fal.media/x.png"

    await ledger.guarded_generate(_gen)
    over = await ledger.guarded_generate(_gen)  # over cap but not enforced

    assert over is not None
    assert calls["n"] == 2  # both calls ran
    assert await _ledger_sum(sqlite_db_session) == 2  # spend exceeded the cap (observability-only)


async def test_cap_zero_disables_ceiling(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.0, cost_per_image_usd=0.01, enforce=True)
    ledger = FalLedger(sqlite_db_session, config=cfg)

    async def _gen():
        return "https://fal.media/x.png"

    for _ in range(3):
        assert await ledger.guarded_generate(_gen) is not None
    assert await _ledger_sum(sqlite_db_session) == 3


# ---------------------------------------------------------------------------
# Persistence across "processes" (separate ledger instances, same session)
# ---------------------------------------------------------------------------

async def test_cap_reads_prior_persisted_spend(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.02, cost_per_image_usd=0.01, enforce=True)

    # Simulate a prior build that already spent 2 cents (= the whole cap).
    seed = FalLedger(sqlite_db_session, config=cfg)
    await seed.record(purpose="qa_image", cost_cents=2, status="charged")

    # A fresh ledger instance (new "process") must see that spend and block.
    fresh = FalLedger(sqlite_db_session, config=cfg)
    snap = await fresh.snapshot()
    assert snap.spent_cents == 2
    assert not snap.can_afford_one()

    async def _gen():
        return "https://fal.media/x.png"

    assert await fresh.guarded_generate(_gen) is None
