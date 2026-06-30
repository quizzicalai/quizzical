"""Persistent FAL spend ledger + hard lifetime $-cap (PRIORITY 1).

This is the cost guardrail prior reviews flagged as MISSING. The existing
``app.services.precompute.cost_guard`` enforces a *per-UTC-day* LLM build budget
off ``precompute_jobs.cost_cents``; the in-memory ``scripts/_precompute_spend``
``SpendLedger`` only lives for one build run. NEITHER is a durable, lifetime,
FAL-only ledger — so repeated builds (or a crash-loop) could quietly overrun the
owner's FAL budget. This module closes that hole.

Contract (the invariant the same-universe pipeline MUST honour):

    No FAL generation proceeds without a PRE-FLIGHT cap check AND a POST-call
    ledger record.

``FalLedger.guarded_generate`` enforces both atomically from the caller's
perspective: it reads the lifetime spend, refuses (or, when ``enforce=False``,
warns) if the next image would breach the cap, runs the supplied async
``generate`` callable only when allowed, and records the spend either way.

Portable: the spend sum is a plain ``SUM(cost_cents)`` so the sqlite test bench
exercises the real logic (no PG-only constructs). The ledger never commits — the
caller owns the transaction, matching every other repository here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import FalSpendLedger

logger = structlog.get_logger(__name__)

# A coroutine that performs one FAL generation and returns the image URL (or
# None on fail-open). Matches ``FalImageClient.generate``'s return contract.
GenerateFn = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True)
class SpendSnapshot:
    """Lifetime FAL spend vs the configured cap (all integer cents)."""

    spent_cents: int
    cap_cents: int
    cost_per_image_cents: float
    enforce: bool

    @property
    def remaining_cents(self) -> int:
        return max(0, self.cap_cents - self.spent_cents)

    @property
    def spent_usd(self) -> float:
        return round(self.spent_cents / 100.0, 4)

    @property
    def cap_usd(self) -> float:
        return round(self.cap_cents / 100.0, 2)

    @property
    def charge_per_image_cents(self) -> int:
        """The integer cents ACTUALLY billed per image (what the ledger records).

        The pre-flight affordability check and the post-call charge must use the
        SAME value, or the cap can either over- or under-block by the rounding
        delta. We round to the nearest cent (a partial-cent image still consumes
        at least 1 cent of budget)."""
        return max(1, int(round(self.cost_per_image_cents)))

    def would_exceed(self, projected_cents: float) -> bool:
        """True iff charging ``projected_cents`` more would breach the cap.

        A ``cap_cents`` of 0 disables the ceiling (treated as "no cap"), so a
        misconfigured-to-zero budget never silently blocks all generation —
        that is an explicit opt-out, not the default (default cap is $150)."""
        if self.cap_cents <= 0:
            return False
        return (self.spent_cents + projected_cents) > self.cap_cents

    def can_afford_one(self) -> bool:
        return not self.would_exceed(self.charge_per_image_cents)


class FalLedger:
    """Repository + guard over ``fal_spend_ledger``.

    Construct one per request/build with the live ``AsyncSession`` and the
    ``FalBudgetConfig`` (``settings.images.fal_budget``)."""

    def __init__(self, session: AsyncSession, *, config) -> None:  # config: FalBudgetConfig
        self.session = session
        self._config = config

    async def total_spent_cents(self) -> int:
        """Lifetime sum of charged cents across the whole ledger."""
        result = await self.session.execute(
            select(func.coalesce(func.sum(FalSpendLedger.cost_cents), 0))
        )
        return int(result.scalar_one() or 0)

    async def snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            spent_cents=await self.total_spent_cents(),
            cap_cents=int(self._config.cap_cents),
            cost_per_image_cents=float(self._config.cost_per_image_cents),
            enforce=bool(self._config.enforce),
        )

    async def record(
        self,
        *,
        purpose: str,
        cost_cents: int,
        status: str = "charged",
        topic_slug: str | None = None,
        prompt_hash: str | None = None,
        fal_request_url: str | None = None,
    ) -> None:
        """Append one row. Flushes (so a subsequent ``total_spent_cents`` in the
        same transaction sees it) but never commits — the caller owns the tx."""
        self.session.add(
            FalSpendLedger(
                purpose=purpose,
                topic_slug=topic_slug,
                prompt_hash=prompt_hash,
                fal_request_url=fal_request_url,
                cost_cents=int(max(0, cost_cents)),
                status=status,
            )
        )
        await self.session.flush()

    async def guarded_generate(
        self,
        generate: GenerateFn,
        *,
        purpose: str = "qa_image",
        topic_slug: str | None = None,
        prompt_hash: str | None = None,
    ) -> str | None:
        """Run ``generate`` ONLY if the lifetime cap allows one more image, and
        record the outcome in the ledger.

        Returns the generated image URL, or ``None`` when the cap blocked the
        call (``enforce=True``) or FAL failed open. The cap decision is made
        from the live DB spend, so it holds across processes and prior builds.
        """
        snap = await self.snapshot()

        if not snap.can_afford_one() and snap.enforce:
            # HARD STOP: do not call FAL. Record a zero-cost 'blocked' audit row.
            logger.warning(
                "fal.budget.blocked",
                purpose=purpose,
                topic_slug=topic_slug,
                spent_usd=snap.spent_usd,
                cap_usd=snap.cap_usd,
            )
            await self.record(
                purpose=purpose,
                cost_cents=0,
                status="blocked",
                topic_slug=topic_slug,
                prompt_hash=prompt_hash,
            )
            return None

        if not snap.can_afford_one() and not snap.enforce:
            logger.warning(
                "fal.budget.over_cap_not_enforced",
                purpose=purpose,
                spent_usd=snap.spent_usd,
                cap_usd=snap.cap_usd,
            )

        # Cap allows it (or enforcement is off) — perform the FAL call.
        url = await generate()

        # Charge the attempt. FAL bills the moment the call is accepted, so we
        # record the cost whether or not a usable URL came back (a None/failed
        # call still consumed quota), matching cost_guard's "every job counts".
        # Use the SAME integer charge the affordability check used.
        charge_cents = snap.charge_per_image_cents
        await self.record(
            purpose=purpose,
            cost_cents=charge_cents,
            status="charged",
            topic_slug=topic_slug,
            prompt_hash=prompt_hash,
            fal_request_url=url,
        )
        return url
