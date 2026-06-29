"""Cost -> speed -> quality decision rule and Pareto frontier.

Given the per-variant aggregates for one function, pick a winner using the
product's stated priority order:

    1. COST first   (minimize $ per call)
    2. SPEED second (minimize p95 wall latency)
    3. QUALITY third (maximize judge score)

A naive "always cheapest" rule would happily ship a broken-but-free config, so
cost-first is implemented *lexicographically with guardrails*:

    Step 0  ELIGIBILITY. A variant is eligible only if it clears the hard gates:
              - validity_rate  >= min_validity      (it actually returns usable output)
              - all required deterministic checks pass at >= check_pass_rate
              - judge quality FLOOR is met using the *CI lower bound* (we require
                the lower 95% bound, not the point mean, to be >= the floor, so we
                don't crown a config whose quality edge is within noise)
              - p95 latency <= latency_budget (if a budget is set)
    Step 1  Among eligible variants, take the cheapest (mean $/call).
    Step 2  Break cost ties (within `cost_tie_pct`) by p95 latency.
    Step 3  Break remaining ties by mean judge quality.

If NO variant is eligible (e.g. nobody clears the quality floor), we report that
explicitly and fall back to the *highest-quality* variant with a warning -- the
framework never silently ships a sub-floor config.

We also expose the **Pareto frontier** over (cost, latency, -quality) so a human
can override the automatic pick when, say, a 2x quality gain is worth a 10%
cost bump. The lexicographic pick is always one point on that frontier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .stats import Estimate


@dataclass
class VariantAggregate:
    """Rolled-up metrics for one variant of one function."""

    variant: str
    model: str
    prompt_strategy: str
    n: int

    cost_usd: Estimate  # mean $/call with CI
    latency_p50_s: float
    latency_p95_s: float
    quality: Estimate  # mean judge agg with CI (CI lower bound is the gate)
    validity_rate: float  # fraction of reps that returned valid output
    check_pass_rates: dict[str, float] = field(default_factory=dict)


@dataclass
class Decision:
    function: str
    winner: VariantAggregate | None
    eligible: list[VariantAggregate]
    rejected: list[tuple[VariantAggregate, str]]  # (variant, reason)
    pareto_front: list[VariantAggregate]
    floor_met: bool
    rationale: str


def _eligibility_reason(
    v: VariantAggregate,
    *,
    quality_floor: float,
    required_checks: tuple[str, ...],
    min_validity: float,
    check_pass_rate: float,
    latency_budget_p95_s: float | None,
) -> str | None:
    """Return None if eligible, else a human-readable rejection reason."""
    if v.validity_rate < min_validity:
        return f"validity {v.validity_rate:.0%} < {min_validity:.0%}"
    for chk in required_checks:
        rate = v.check_pass_rates.get(chk, 0.0)
        if rate < check_pass_rate:
            return f"check '{chk}' pass-rate {rate:.0%} < {check_pass_rate:.0%}"
    if v.quality.lo < quality_floor:
        return (
            f"quality CI-lower {v.quality.lo:.2f} < floor {quality_floor:.2f} "
            f"(mean {v.quality.mean:.2f})"
        )
    if latency_budget_p95_s is not None and v.latency_p95_s > latency_budget_p95_s:
        return f"p95 latency {v.latency_p95_s:.1f}s > budget {latency_budget_p95_s:.1f}s"
    return None


def pareto_frontier(variants: list[VariantAggregate]) -> list[VariantAggregate]:
    """Variants not dominated on (cost down, p95 latency down, quality up).

    ``a`` dominates ``b`` if a is no worse on all three objectives and strictly
    better on at least one.
    """
    front: list[VariantAggregate] = []
    for v in variants:
        dominated = False
        for w in variants:
            if w is v:
                continue
            no_worse = (
                w.cost_usd.mean <= v.cost_usd.mean
                and w.latency_p95_s <= v.latency_p95_s
                and w.quality.mean >= v.quality.mean
            )
            strictly_better = (
                w.cost_usd.mean < v.cost_usd.mean
                or w.latency_p95_s < v.latency_p95_s
                or w.quality.mean > v.quality.mean
            )
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            front.append(v)
    return front


def decide(
    function: str,
    variants: list[VariantAggregate],
    *,
    quality_floor: float = 4.0,
    required_checks: tuple[str, ...] = (),
    min_validity: float = 0.95,
    check_pass_rate: float = 0.98,
    latency_budget_p95_s: float | None = None,
    cost_tie_pct: float = 0.10,
) -> Decision:
    """Apply the lexicographic cost -> speed -> quality rule. See module docstring."""
    eligible: list[VariantAggregate] = []
    rejected: list[tuple[VariantAggregate, str]] = []
    for v in variants:
        reason = _eligibility_reason(
            v,
            quality_floor=quality_floor,
            required_checks=required_checks,
            min_validity=min_validity,
            check_pass_rate=check_pass_rate,
            latency_budget_p95_s=latency_budget_p95_s,
        )
        (rejected.append((v, reason)) if reason else eligible.append(v))

    front = pareto_frontier(variants)

    if not eligible:
        # Nobody clears the floor. Surface the best-quality option but flag it.
        fallback = max(variants, key=lambda v: v.quality.mean) if variants else None
        return Decision(
            function=function,
            winner=fallback,
            eligible=[],
            rejected=rejected,
            pareto_front=front,
            floor_met=False,
            rationale=(
                "NO variant met the quality floor "
                f"(CI-lower >= {quality_floor:.2f}). Falling back to highest mean "
                f"quality ({fallback.variant if fallback else 'n/a'}); do NOT ship "
                "until the floor is met or explicitly lowered."
            ),
        )

    # Step 1: cheapest among eligible.
    cheapest = min(v.cost_usd.mean for v in eligible)
    # Step 2: keep all within cost_tie_pct of cheapest, break by p95 latency.
    cost_band = [
        v for v in eligible if v.cost_usd.mean <= cheapest * (1.0 + cost_tie_pct)
    ]
    fastest = min(v.latency_p95_s for v in cost_band)
    speed_band = [v for v in cost_band if v.latency_p95_s <= fastest * 1.05]
    # Step 3: highest quality among the cost+speed band.
    winner = max(speed_band, key=lambda v: v.quality.mean)

    rationale = (
        f"Cost-first: {len(eligible)} variant(s) cleared the floor "
        f"(quality CI-lower >= {quality_floor:.2f}"
        + (f", p95 <= {latency_budget_p95_s:.1f}s" if latency_budget_p95_s else "")
        + f"). Cheapest band (within {cost_tie_pct:.0%} of "
        f"${cheapest*1000:.3f}/1k calls) -> fastest p95 -> best quality. "
        f"Winner: {winner.variant} "
        f"(${winner.cost_usd.mean*1000:.3f}/1k, p95 {winner.latency_p95_s:.1f}s, "
        f"quality {winner.quality.mean:.2f})."
    )
    return Decision(
        function=function,
        winner=winner,
        eligible=eligible,
        rejected=rejected,
        pareto_front=front,
        floor_met=True,
        rationale=rationale,
    )
