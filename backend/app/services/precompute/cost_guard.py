"""§21 Phase 3 — daily-budget cost guard for the build worker.

Two ACs:
- `AC-PRECOMP-BUILD-5`: a hard daily $-cap (`daily_budget_usd`). The worker
  performs a "pre-attempt" check before each new tier attempt. When the cap
  is met, the in-flight job is re-queued with `delayed_until=tomorrow_utc`.
- `AC-PRECOMP-COST-6`: at ≥ 75 % of `daily_budget_usd` spent today, Tier-3
  (`strong+search`) escalation is suppressed; the build either passes on
  cheaper tiers or is rejected.

Both checks read the same `precompute_jobs.cost_cents` rolling sum for the
calendar UTC day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import PrecomputeJob


def _utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Inclusive lower / exclusive upper bound for the UTC calendar day."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = datetime.combine(now_utc.date(), time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _next_utc_midnight(now: datetime | None = None) -> datetime:
    return _utc_day_bounds(now)[1]


@dataclass(frozen=True)
class BudgetSnapshot:
    """Rolling-day spend snapshot used by both ACs."""

    spent_cents: int
    daily_cap_cents: int
    tier3_cap_cents: int  # 75 % of `daily_cap_cents` by default

    @property
    def remaining_cents(self) -> int:
        return max(0, self.daily_cap_cents - self.spent_cents)

    def can_attempt(self) -> bool:
        """`AC-PRECOMP-BUILD-5` — strictly under the daily cap."""
        return self.spent_cents < self.daily_cap_cents

    def can_use_tier3(self) -> bool:
        """`AC-PRECOMP-COST-6` — strictly under the Tier-3 cutoff."""
        return self.spent_cents < self.tier3_cap_cents


async def today_spend_cents(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Sum of `precompute_jobs.cost_cents` for the current UTC day.

    Counts every job (succeeded / failed / running / rejected) — the spend
    happened the moment the LLM call billed, regardless of outcome.
    """
    start, end = _utc_day_bounds(now)
    result = await db.execute(
        select(func.coalesce(func.sum(PrecomputeJob.cost_cents), 0)).where(
            PrecomputeJob.created_at >= start, PrecomputeJob.created_at < end
        )
    )
    return int(result.scalar_one() or 0)


async def snapshot(
    db: AsyncSession,
    *,
    daily_budget_usd: float,
    tier3_budget_pct: float = 0.75,
    now: datetime | None = None,
) -> BudgetSnapshot:
    """Construct a `BudgetSnapshot` for the current UTC day.

    `tier3_budget_pct` is clamped to `[0, 1]`; bogus operator config can't
    accidentally turn the Tier-3 cutoff into a no-op or a negative value.
    """
    daily_cents = max(0, int(round(float(daily_budget_usd) * 100)))
    pct = max(0.0, min(1.0, float(tier3_budget_pct)))
    return BudgetSnapshot(
        spent_cents=await today_spend_cents(db, now=now),
        daily_cap_cents=daily_cents,
        tier3_cap_cents=int(round(daily_cents * pct)),
    )


def next_attempt_delay(now: datetime | None = None) -> datetime:
    """`AC-PRECOMP-BUILD-5` — schedule re-queue at next UTC midnight."""
    return _next_utc_midnight(now)
