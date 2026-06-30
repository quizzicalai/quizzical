"""Persistent FAL spend ledger + hard lifetime $-cap (PRIORITY 1).

This is the cost guardrail prior reviews flagged as MISSING. The existing
``app.services.precompute.cost_guard`` enforces a *per-UTC-day* LLM build budget
off ``precompute_jobs.cost_cents``; the in-memory ``scripts/_precompute_spend``
``SpendLedger`` only lives for one build run. NEITHER is a durable, lifetime,
FAL-only ledger — so repeated builds (or a crash-loop) could quietly overrun the
owner's FAL budget. This module closes that hole.

Contract (the invariant the same-universe pipeline MUST honour):

    No FAL generation proceeds without a PRE-FLIGHT cap check AND a POST-call
    ledger record — and a charge is recorded ONLY when FAL actually made a
    billable generate call.

Correctness fixes (2026-06-30 review):
  * LOSSLESS spend (#2): cost is summed in **micro-cents** (1 cent = 1000
    micros). $0.011 = 1.1¢ = 1100 micros is recorded EXACTLY, so the lifetime
    SUM equals true spend and the $150 cap is real (no ~$165 over/under-rounding).
  * NO PHANTOM CHARGES (#3): the caller's ``generate`` returns a
    ``GenerateResult(url, billed)``. The ledger charges iff ``billed`` is True
    (FAL actually ran a billable call). An early/empty return — no FAL key, gen
    disabled, blank prompt, connection failure — is ``billed=False`` and costs $0.
  * ATOMIC check+record (#4): ``guarded_generate`` takes a row-level lock on a
    per-purpose running-total row (``FalSpendCounter``) with ``SELECT ... FOR
    UPDATE`` so concurrent builds serialise on the cap decision (true on
    Postgres; a harmless no-op on the sqlite test bench, where builds are
    single-process anyway).

Portable: the spend sum is a plain ``SUM`` so the sqlite test bench exercises
the real logic. The ledger never commits — the caller owns the transaction,
matching every other repository here.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import FalSpendCounter, FalSpendLedger

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GenerateResult:
    """Outcome of one FAL generate attempt.

    ``url`` is the (validated) image URL or None. ``billed`` is True ONLY when
    FAL actually performed a billable generation — i.e. the call reached FAL and
    completed. A non-billable early/empty return (no key, gen disabled, blank
    prompt, or a connection failure that never reached FAL's billing) is
    ``billed=False`` and costs $0, so it never inflates spend or eats the cap."""

    url: str | None
    billed: bool


# A coroutine performing one FAL generation. Returns ``GenerateResult`` so the
# ledger can charge ONLY a genuinely-billable call (no phantom charges).
GenerateFn = Callable[[], Awaitable[GenerateResult]]


@dataclass(frozen=True)
class SpendSnapshot:
    """Lifetime FAL spend vs the configured cap (all integer micro-cents)."""

    spent_micros: int
    cap_micros: int
    cost_per_image_micros: int
    enforce: bool

    @property
    def remaining_micros(self) -> int:
        return max(0, self.cap_micros - self.spent_micros)

    @property
    def spent_cents(self) -> int:
        """Human-readable cents (rounded) — for logs/audit, NOT the cap unit."""
        return int(round(self.spent_micros / 1000.0))

    @property
    def spent_usd(self) -> float:
        return round(self.spent_micros / 100_000.0, 4)

    @property
    def cap_usd(self) -> float:
        return round(self.cap_micros / 100_000.0, 2)

    @property
    def charge_per_image_micros(self) -> int:
        """Micro-cents billed per image — the SAME value the affordability check
        and the recorded charge use, so the cap can't drift by a rounding delta.
        At least 1 micro (a non-zero-cost image always consumes some budget)."""
        return max(1, int(self.cost_per_image_micros))

    def would_exceed(self, projected_micros: int) -> bool:
        """True iff charging ``projected_micros`` more would breach the cap.

        A ``cap_micros`` of 0 disables the ceiling (treated as "no cap"), so a
        misconfigured-to-zero budget never silently blocks all generation —
        that is an explicit opt-out, not the default (default cap is $150)."""
        if self.cap_micros <= 0:
            return False
        return (self.spent_micros + projected_micros) > self.cap_micros

    def can_afford_one(self) -> bool:
        return not self.would_exceed(self.charge_per_image_micros)


class FalLedger:
    """Repository + guard over ``fal_spend_ledger``.

    Construct one per request/build with the live ``AsyncSession`` and the
    ``FalBudgetConfig`` (``settings.images.fal_budget``)."""

    def __init__(self, session: AsyncSession, *, config) -> None:  # config: FalBudgetConfig
        self.session = session
        self._config = config

    async def total_spent_micros(self) -> int:
        """Lifetime sum of charged micro-cents across the whole ledger."""
        result = await self.session.execute(
            select(func.coalesce(func.sum(FalSpendLedger.cost_micros), 0))
        )
        return int(result.scalar_one() or 0)

    async def snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            spent_micros=await self.total_spent_micros(),
            cap_micros=int(self._config.cap_micros),
            cost_per_image_micros=int(self._config.cost_per_image_micros),
            enforce=bool(self._config.enforce),
        )

    async def record(
        self,
        *,
        purpose: str,
        cost_micros: int,
        status: str = "charged",
        topic_slug: str | None = None,
        prompt_hash: str | None = None,
        fal_request_url: str | None = None,
    ) -> None:
        """Append one row. Flushes (so a subsequent ``total_spent_micros`` in the
        same transaction sees it) but never commits — the caller owns the tx.

        ``cost_cents`` is stored as a rounded-up human-readable mirror; the cap
        math uses ``cost_micros`` only."""
        micros = int(max(0, cost_micros))
        self.session.add(
            FalSpendLedger(
                purpose=purpose,
                topic_slug=topic_slug,
                prompt_hash=prompt_hash,
                fal_request_url=fal_request_url,
                cost_micros=micros,
                # ceil so the human-readable mirror never *under*-reports a spend.
                cost_cents=int(math.ceil(micros / 1000.0)),
                status=status,
            )
        )
        await self.session.flush()

    async def _lock_counter(self, purpose: str) -> None:
        """Serialise the check+record across concurrent builds (#4).

        Locks (or creates) a single per-purpose running-total row with
        ``SELECT ... FOR UPDATE``; on Postgres this blocks any other transaction
        in ``guarded_generate`` for the same purpose until this one commits, so
        the SUM-then-INSERT sequence is atomic w.r.t. the cap. ``FOR UPDATE`` is
        a no-op on the sqlite test bench (single-process), where it is not
        needed. Best-effort: a lock failure must never break a build."""
        try:
            row = (
                await self.session.execute(
                    select(FalSpendCounter)
                    .where(FalSpendCounter.purpose == purpose)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                self.session.add(FalSpendCounter(purpose=purpose))
                await self.session.flush()
        except Exception:  # noqa: BLE001 — locking is defence-in-depth, never fatal
            logger.warning("fal.budget.lock_failed", purpose=purpose, exc_info=True)

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
        call (``enforce=True``), FAL failed open, or no billable call was made.
        The cap decision is made from the live DB spend under a per-purpose row
        lock, so it is atomic across concurrent builds and holds across processes
        and prior builds.
        """
        # Take the per-purpose lock FIRST so the snapshot we read and the row we
        # write are serialised against any concurrent guarded_generate (#4).
        await self._lock_counter(purpose)

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
                cost_micros=0,
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
        result = await generate()

        if not result.billed:
            # No billable FAL call happened (no key, gen disabled, blank prompt,
            # or a failure before FAL billed). Do NOT charge — that would inflate
            # spend and prematurely exhaust the cap (#3). Audit it at 0 cost.
            logger.info(
                "fal.budget.not_billed",
                purpose=purpose,
                topic_slug=topic_slug,
                has_url=bool(result.url),
            )
            await self.record(
                purpose=purpose,
                cost_micros=0,
                status="reused" if result.url else "blocked",
                topic_slug=topic_slug,
                prompt_hash=prompt_hash,
                fal_request_url=result.url,
            )
            return result.url

        # A genuinely billable call occurred — record the true micro-cent charge
        # (FAL bills an accepted+completed generation whether or not a usable URL
        # came back). Use the SAME value the affordability check used.
        await self.record(
            purpose=purpose,
            cost_micros=snap.charge_per_image_micros,
            status="charged",
            topic_slug=topic_slug,
            prompt_hash=prompt_hash,
            fal_request_url=result.url,
        )
        return result.url
