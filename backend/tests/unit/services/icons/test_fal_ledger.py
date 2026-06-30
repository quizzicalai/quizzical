"""FAL spend ledger + hard lifetime $-cap tests (PRIORITY 1 — the cost guard).

The invariant under test: no FAL generation proceeds without a pre-flight cap
check + a post-call ledger record; the cap is enforced off the PERSISTENT DB
spend (so it holds across builds/processes); spend is LOSSLESS (micro-cents); and
a charge is recorded ONLY when FAL actually made a billable call (no phantoms).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import FalSpendLedger
from app.services.icons.fal_ledger import FalLedger, GenerateResult
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


class _Budget:
    """Stand-in for settings.images.fal_budget (micro-cent aware)."""

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

    @property
    def cap_micros(self):
        return int(round(self.cap_usd * 100_000))

    @property
    def cost_per_image_micros(self):
        return int(round(self.cost_per_image_usd * 100_000))


def _billed(url):
    async def _gen():
        return GenerateResult(url=url, billed=True)
    return _gen


async def _ledger_rows(session: AsyncSession):
    return (await session.execute(select(FalSpendLedger))).scalars().all()


async def _ledger_sum_micros(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(FalSpendLedger.cost_micros), 0))
            )
        ).scalar_one()
        or 0
    )


# ---------------------------------------------------------------------------
# Records every billable charge; sum is persistent + LOSSLESS
# ---------------------------------------------------------------------------

async def test_guarded_generate_records_charge(sqlite_db_session: AsyncSession):
    ledger = FalLedger(sqlite_db_session, config=_Budget())
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return GenerateResult(url="https://fal.media/abc.png", billed=True)

    url = await ledger.guarded_generate(_gen, purpose="qa_image", topic_slug="harry-potter")
    assert url == "https://fal.media/abc.png"
    assert calls["n"] == 1

    rows = [r for r in await _ledger_rows(sqlite_db_session) if r.status == "charged"]
    assert len(rows) == 1
    # LOSSLESS: $0.011 = 1.1c = 1100 micros recorded EXACTLY (no rounding loss).
    assert rows[0].cost_micros == 1100
    assert rows[0].cost_cents == 2  # human-readable mirror, ceil(1100/1000)
    assert rows[0].purpose == "qa_image"
    assert rows[0].topic_slug == "harry-potter"
    assert rows[0].fal_request_url == "https://fal.media/abc.png"


async def test_lifetime_sum_is_lossless_over_many_images(sqlite_db_session: AsyncSession):
    """100 images at $0.011 must sum to EXACTLY $1.10 (not $1.00 from per-row
    truncation, nor $2.00 from per-row round-up)."""
    ledger = FalLedger(sqlite_db_session, config=_Budget())
    for _ in range(100):
        await ledger.guarded_generate(_billed("https://fal.media/x.png"))
    total_micros = await _ledger_sum_micros(sqlite_db_session)
    assert total_micros == 100 * 1100  # 110_000 micros == $1.10 exactly
    snap = await ledger.snapshot()
    assert snap.spent_usd == 1.10


async def test_billed_failed_url_still_charges(sqlite_db_session: AsyncSession):
    """A billable call that returns no usable URL (timeout/NSFW post-reject) still
    consumed quota => charged."""
    ledger = FalLedger(sqlite_db_session, config=_Budget())

    async def _gen():
        return GenerateResult(url=None, billed=True)

    url = await ledger.guarded_generate(_gen)
    assert url is None
    charged = [r for r in await _ledger_rows(sqlite_db_session) if r.status == "charged"]
    assert len(charged) == 1
    assert charged[0].cost_micros == 1100


# ---------------------------------------------------------------------------
# NO PHANTOM CHARGES (#3) — a non-billable call costs $0
# ---------------------------------------------------------------------------

async def test_non_billable_call_is_not_charged(sqlite_db_session: AsyncSession):
    """No FAL key / gen disabled / connection failure => billed=False => $0
    charge, and the cap is NOT consumed (the 'flag-on, no key' scenario)."""
    ledger = FalLedger(sqlite_db_session, config=_Budget())
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return GenerateResult(url=None, billed=False)

    # Run many non-billable attempts; spend must stay at $0.
    for _ in range(50):
        assert await ledger.guarded_generate(_gen) is None
    assert calls["n"] == 50
    assert await _ledger_sum_micros(sqlite_db_session) == 0
    snap = await ledger.snapshot()
    assert snap.spent_usd == 0.0
    # No row is ever 'charged'.
    assert all(r.status != "charged" for r in await _ledger_rows(sqlite_db_session))


# ---------------------------------------------------------------------------
# Hard cap enforcement (in micros)
# ---------------------------------------------------------------------------

async def test_cap_blocks_when_exhausted(sqlite_db_session: AsyncSession):
    # Cap = 1 cent; cost per image = 1 cent. First call fits; second is blocked.
    cfg = _Budget(cap_usd=0.01, cost_per_image_usd=0.01, enforce=True)
    ledger = FalLedger(sqlite_db_session, config=cfg)
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return GenerateResult(url=f"https://fal.media/{calls['n']}.png", billed=True)

    first = await ledger.guarded_generate(_gen)
    second = await ledger.guarded_generate(_gen)

    assert first is not None
    assert second is None  # cap refused it
    assert calls["n"] == 1  # FAL was NOT called the second time

    statuses = sorted(r.status for r in await _ledger_rows(sqlite_db_session))
    assert statuses == ["blocked", "charged"]
    # Spend never exceeds the cap: 1 cent = 1000 micros.
    assert await _ledger_sum_micros(sqlite_db_session) == 1000


async def test_cap_is_exact_at_150_no_overshoot(sqlite_db_session: AsyncSession):
    """The $150 cap must be REAL: at $0.011/img the last affordable image is the
    one that keeps lifetime spend <= $150, and the next is blocked — no ~$165
    overshoot from rounding."""
    cfg = _Budget(cap_usd=150.0, cost_per_image_usd=0.011, enforce=True)
    ledger = FalLedger(sqlite_db_session, config=cfg)
    # 150 / 0.011 = 13636.36 -> 13636 images fit (=$149.996), the 13637th would
    # be $150.007 > cap and must be blocked. Seed 13636 charged rows directly.
    await ledger.record(purpose="qa_image", cost_micros=13636 * 1100, status="charged")
    snap = await ledger.snapshot()
    assert snap.spent_micros == 13636 * 1100  # 14_999_600 micros = $149.996
    assert snap.can_afford_one() is False  # +1100 would exceed 15_000_000
    assert await ledger.guarded_generate(_billed("https://fal.media/x.png")) is None
    # Lifetime spend never crossed the $150 (= 15_000_000 micros) ceiling.
    assert await _ledger_sum_micros(sqlite_db_session) <= 15_000_000


async def test_enforce_false_records_but_never_blocks(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.01, cost_per_image_usd=0.01, enforce=False)
    ledger = FalLedger(sqlite_db_session, config=cfg)
    calls = {"n": 0}

    async def _gen():
        calls["n"] += 1
        return GenerateResult(url="https://fal.media/x.png", billed=True)

    await ledger.guarded_generate(_gen)
    over = await ledger.guarded_generate(_gen)  # over cap but not enforced

    assert over is not None
    assert calls["n"] == 2  # both calls ran
    assert await _ledger_sum_micros(sqlite_db_session) == 2000  # 2 cents (observability-only)


async def test_cap_zero_disables_ceiling(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.0, cost_per_image_usd=0.01, enforce=True)
    ledger = FalLedger(sqlite_db_session, config=cfg)

    for _ in range(3):
        assert await ledger.guarded_generate(_billed("https://fal.media/x.png")) is not None
    assert await _ledger_sum_micros(sqlite_db_session) == 3000


# ---------------------------------------------------------------------------
# Persistence across "processes" (separate ledger instances, same session)
# ---------------------------------------------------------------------------

async def test_charge_is_model_size_aware(sqlite_db_session: AsyncSession):
    """Blackbox #3 — when guarded_generate is given a model + size, the recorded
    charge is the model+size-aware cost, NOT the flat config constant. A 1024px
    FLUX-dev image charges ~2621 micros (~$0.025); a 256px schnell ~20 micros."""
    ledger = FalLedger(sqlite_db_session, config=_Budget(cost_per_image_usd=0.011))

    # FLUX dev hero @ 1024x1024.
    await ledger.guarded_generate(
        _billed("https://fal.media/dev.png"),
        model="fal-ai/flux/dev", image_size={"width": 1024, "height": 1024},
    )
    # FLUX schnell thumb @ 256x256.
    await ledger.guarded_generate(
        _billed("https://fal.media/thumb.png"),
        model="fal-ai/flux/schnell", image_size={"width": 256, "height": 256},
    )

    charged = sorted(
        (r.cost_micros for r in await _ledger_rows(sqlite_db_session) if r.status == "charged")
    )
    assert charged == [20, 2621]  # NOT [1100, 1100] (the old flat constant)


async def test_legacy_charge_without_model_uses_config_constant(
    sqlite_db_session: AsyncSession,
):
    """Omitting model+size preserves the legacy flat ``cost_per_image_usd`` charge
    (so existing call sites are unchanged)."""
    ledger = FalLedger(sqlite_db_session, config=_Budget(cost_per_image_usd=0.011))
    await ledger.guarded_generate(_billed("https://fal.media/x.png"))
    charged = [r for r in await _ledger_rows(sqlite_db_session) if r.status == "charged"]
    assert charged[0].cost_micros == 1100  # the config constant, unchanged


async def test_cap_reads_prior_persisted_spend(sqlite_db_session: AsyncSession):
    cfg = _Budget(cap_usd=0.02, cost_per_image_usd=0.01, enforce=True)

    # Simulate a prior build that already spent 2 cents (= the whole cap).
    seed = FalLedger(sqlite_db_session, config=cfg)
    await seed.record(purpose="qa_image", cost_micros=2000, status="charged")

    # A fresh ledger instance (new "process") must see that spend and block.
    fresh = FalLedger(sqlite_db_session, config=cfg)
    snap = await fresh.snapshot()
    assert snap.spent_cents == 2
    assert not snap.can_afford_one()

    assert await fresh.guarded_generate(_billed("https://fal.media/x.png")) is None
