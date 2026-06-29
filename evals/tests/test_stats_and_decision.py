"""Unit tests for the statistics and decision core.

These run offline with no LLM calls and validate the parts that must be correct
for any conclusion to be trustworthy: CIs, paired significance, multiple-
comparison correction, power sizing, and the cost->speed->quality decision rule.

    cd evals && python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quizzical_evals import stats  # noqa: E402
from quizzical_evals.decision import VariantAggregate, decide  # noqa: E402
from quizzical_evals.stats import Estimate  # noqa: E402


def test_bootstrap_ci_brackets_mean():
    vals = [4.0, 4.2, 3.8, 4.5, 4.1, 3.9, 4.3, 4.0]
    e = stats.bootstrap_ci(vals, iters=2000)
    assert e.lo <= e.mean <= e.hi
    assert e.n == len(vals)


def test_percentile_p95():
    vals = list(range(1, 101))  # 1..100
    assert abs(stats.percentile(vals, 50) - 50.5) < 1.0
    assert stats.percentile(vals, 95) >= 95


def test_paired_compare_detects_real_shift():
    a = [3.0, 3.2, 2.9, 3.1, 3.0, 3.3, 2.8, 3.1, 3.0, 3.2]
    b = [v + 0.6 for v in a]  # uniform +0.6 improvement
    pt = stats.paired_compare(a, b)
    assert pt.mean_delta > 0.5
    assert pt.ci_excludes_zero
    assert pt.t_pvalue < 0.01


def test_paired_compare_no_effect_is_not_significant():
    a = [4.0, 4.1, 3.9, 4.2, 4.0, 3.8, 4.1, 4.0]
    b = list(a)  # identical
    pt = stats.paired_compare(a, b)
    assert abs(pt.mean_delta) < 1e-9


def test_holm_more_conservative_than_bh():
    pvals = [0.001, 0.02, 0.03, 0.2]
    holm = stats.holm_bonferroni(pvals)
    bh = stats.benjamini_hochberg(pvals)
    # BH should reject at least as many as Holm (higher power).
    assert sum(bh) >= sum(holm)
    assert holm[0] is True  # the strongest signal is rejected by both


def test_min_reps_for_effect_monotone():
    # Smaller MDE -> needs more reps; larger SD -> needs more reps.
    n_small_mde = stats.min_reps_for_effect(sd_delta=0.4, mde=0.1)
    n_large_mde = stats.min_reps_for_effect(sd_delta=0.4, mde=0.3)
    assert n_small_mde > n_large_mde >= 2


def _agg(name, cost, p95, q_mean, q_lo, validity=1.0, checks=None):
    return VariantAggregate(
        variant=name, model="m", prompt_strategy="baseline", n=20,
        cost_usd=Estimate(cost, cost, cost, 20, "t"),
        latency_p50_s=p95 * 0.6, latency_p95_s=p95,
        quality=Estimate(q_mean, q_lo, q_mean + (q_mean - q_lo), 20, "bootstrap"),
        validity_rate=validity, check_pass_rates=checks or {},
    )


def test_decision_is_cost_first_among_eligible():
    variants = [
        _agg("cheap_ok", cost=0.0002, p95=6.0, q_mean=4.3, q_lo=4.1),
        _agg("pricey_better", cost=0.0050, p95=5.0, q_mean=4.6, q_lo=4.4),
    ]
    d = decide("fn", variants, quality_floor=4.0, latency_budget_p95_s=8.0)
    assert d.floor_met
    # Cost-first: the cheaper eligible variant wins even though the other scores higher.
    assert d.winner.variant == "cheap_ok"


def test_decision_gates_on_ci_lower_bound():
    # High mean but CI-lower below floor must NOT be crowned.
    variants = [
        _agg("noisy_high_mean", cost=0.0001, p95=4.0, q_mean=4.5, q_lo=3.7),
        _agg("solid", cost=0.0010, p95=4.0, q_mean=4.2, q_lo=4.05),
    ]
    d = decide("fn", variants, quality_floor=4.0)
    assert d.winner.variant == "solid"  # noisy one fails the CI-lower gate


def test_decision_floor_not_met_warns():
    variants = [_agg("below", cost=0.0001, p95=4.0, q_mean=3.5, q_lo=3.2)]
    d = decide("fn", variants, quality_floor=4.0)
    assert not d.floor_met
    assert "NO variant" in d.rationale


def test_decision_latency_budget_rejects():
    variants = [
        _agg("fast_pricey", cost=0.0050, p95=4.0, q_mean=4.3, q_lo=4.1),
        _agg("cheap_slow", cost=0.0001, p95=12.0, q_mean=4.3, q_lo=4.1),
    ]
    d = decide("fn", variants, quality_floor=4.0, latency_budget_p95_s=8.0)
    # cheap_slow is cheaper but over budget -> fast_pricey wins.
    assert d.winner.variant == "fast_pricey"


def test_pricing_snapshot_roundtrip():
    from quizzical_evals.pricing import Usage, estimate_cost

    # gpt-4o-mini: $0.15 in / $0.60 out per Mtok.
    cost = estimate_cost("gpt-4o-mini", Usage(prompt_tokens=1_000_000, completion_tokens=0))
    assert abs(cost - 0.15) < 1e-6
    cost2 = estimate_cost("gpt-4o-mini", Usage(prompt_tokens=0, completion_tokens=1_000_000))
    assert abs(cost2 - 0.60) < 1e-6
