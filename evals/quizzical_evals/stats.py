"""Statistics for non-deterministic LLM evaluation.

The whole point of this module is to let us make defensible *config decisions*
despite LLM output being random. Everything here answers one of three questions:

  1. "What is the metric, with uncertainty?"   -> mean + confidence interval
  2. "Is variant B really different from A?"   -> paired significance test
  3. "How many reps do I need to see effect X?" -> power / minimum-detectable-effect

Design choices (justified in evals/methodology.md):

* **Bootstrap percentile CI is the default** for means. LLM-judge scores are
  bounded (1-5), discrete, and skewed, so the normal/t approximation can be
  poor at small n; the bootstrap makes no distributional assumption. We also
  expose a Student-t interval for quick sanity checks and for cost/latency
  (which are continuous and roughly log-normal -> we CI the mean directly and
  recommend reporting medians for latency).

* **Paired design.** Every variant runs on the *same* inputs (and same rep
  seeds where the harness allows), so comparisons are paired: we test the
  per-input delta vector, which removes input-difficulty variance and is far
  more powerful than unpaired tests.

* **Paired t for the delta, Wilcoxon signed-rank as a non-parametric backstop.**
  We report both; if they disagree the effect is fragile.

* **Multiple-comparison correction.** Comparing k variants pairwise inflates
  false positives; we expose Holm-Bonferroni (controls FWER, conservative) and
  Benjamini-Hochberg (controls FDR, higher power). The report uses BH for the
  exploratory variant sweep and Holm for the final go/no-go on the chosen
  config.

scipy is used when available (it is, in the repo venv) and we fall back to
stdlib implementations so the module imports and runs anywhere.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

try:  # scipy is available in the backend venv; degrade gracefully if not.
    from scipy import stats as _scipy_stats  # type: ignore

    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _scipy_stats = None  # type: ignore
    _HAVE_SCIPY = False


# ---------------------------------------------------------------------------
# Point estimates + confidence intervals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    mean: float
    lo: float
    hi: float
    n: int
    method: str

    @property
    def half_width(self) -> float:
        return (self.hi - self.lo) / 2.0


def bootstrap_ci(
    values: list[float],
    *,
    iters: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0xC0FFEE,
    statistic: str = "mean",
) -> Estimate:
    """Percentile bootstrap CI for the mean (or median).

    10,000 resamples is the field-standard default (see methodology refs). The
    seed is fixed so a report is reproducible.
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return Estimate(0.0, 0.0, 0.0, 0, f"bootstrap-{statistic}")
    agg = statistics.median if statistic == "median" else statistics.fmean
    if n == 1:
        return Estimate(agg(vals), vals[0], vals[0], 1, f"bootstrap-{statistic}")
    rng = random.Random(seed)
    boots: list[float] = []
    for _ in range(iters):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        boots.append(agg(sample))
    boots.sort()
    lo = boots[int((alpha / 2) * iters)]
    hi = boots[int((1 - alpha / 2) * iters)]
    return Estimate(agg(vals), lo, hi, n, f"bootstrap-{statistic}")


def t_interval(values: list[float], *, alpha: float = 0.05) -> Estimate:
    """Student-t CI for the mean. Good for continuous metrics (cost/latency)."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return Estimate(0.0, 0.0, 0.0, 0, "t-interval")
    mean = statistics.fmean(vals)
    if n == 1:
        return Estimate(mean, mean, mean, 1, "t-interval")
    sd = statistics.stdev(vals)
    se = sd / math.sqrt(n)
    tcrit = _t_ppf(1 - alpha / 2, n - 1)
    return Estimate(mean, mean - tcrit * se, mean + tcrit * se, n, "t-interval")


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0,100]). Used for latency p50/p95."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    rank = (q / 100.0) * (len(vals) - 1)
    lo_i = int(math.floor(rank))
    hi_i = int(math.ceil(rank))
    if lo_i == hi_i:
        return vals[lo_i]
    frac = rank - lo_i
    return vals[lo_i] * (1 - frac) + vals[hi_i] * frac


# ---------------------------------------------------------------------------
# Paired significance tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairedTest:
    mean_delta: float  # mean(b) - mean(a) over paired inputs
    ci_lo: float
    ci_hi: float
    t_pvalue: float
    wilcoxon_pvalue: float | None
    n_pairs: int

    @property
    def ci_excludes_zero(self) -> bool:
        return self.ci_lo > 0 or self.ci_hi < 0


def paired_compare(
    a: list[float], b: list[float], *, alpha: float = 0.05, seed: int = 0xBEEF
) -> PairedTest:
    """Compare paired samples ``b`` vs ``a`` (same inputs, same order).

    Returns the mean paired delta (b - a) with a bootstrap CI on the delta, a
    paired-t p-value, and a Wilcoxon signed-rank p-value (non-parametric backstop).
    A directional win requires the delta CI to exclude zero AND the tests to agree.
    """
    if len(a) != len(b):
        raise ValueError(f"paired_compare needs equal-length samples: {len(a)} vs {len(b)}")
    deltas = [float(bi) - float(ai) for ai, bi in zip(a, b)]
    n = len(deltas)
    ci = bootstrap_ci(deltas, alpha=alpha, seed=seed)
    t_p = _paired_t_pvalue(a, b)
    w_p = _wilcoxon_pvalue(deltas)
    return PairedTest(
        mean_delta=statistics.fmean(deltas) if deltas else 0.0,
        ci_lo=ci.lo,
        ci_hi=ci.hi,
        t_pvalue=t_p,
        wilcoxon_pvalue=w_p,
        n_pairs=n,
    )


# ---------------------------------------------------------------------------
# Multiple-comparison correction
# ---------------------------------------------------------------------------


def holm_bonferroni(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Holm-Bonferroni step-down. Controls family-wise error rate (FWER).

    Returns a reject/accept mask aligned with the input order. Conservative;
    use for the final go/no-go decision where a false positive is costly.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    reject = [False] * m
    for rank, idx in enumerate(order):
        threshold = alpha / (m - rank)
        if pvalues[idx] <= threshold:
            reject[idx] = True
        else:
            break  # step-down: once we fail to reject, stop
    return reject


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg step-up. Controls false discovery rate (FDR).

    Higher power than Holm; use for the exploratory variant sweep where we
    tolerate a controlled fraction of false leads.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    reject = [False] * m
    max_rank = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= (rank / m) * alpha:
            max_rank = rank
    if max_rank >= 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= max_rank:
                reject[idx] = True
    return reject


# ---------------------------------------------------------------------------
# Power / minimum detectable effect (rep planning)
# ---------------------------------------------------------------------------


def min_reps_for_effect(
    sd_delta: float, mde: float, *, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Reps (paired) needed to detect a mean paired delta ``mde`` at given power.

    Uses the standard normal approximation for a paired/one-sample test:
        n = ((z_{1-a/2} + z_{1-b}) * sd / mde)^2
    ``sd_delta`` is the SD of the per-input delta vector (estimate it from a
    pilot run). Returns a ceiling-rounded integer >= 2.
    """
    if mde <= 0:
        raise ValueError("mde must be positive")
    if sd_delta <= 0:
        return 2
    z_a = _normal_ppf(1 - alpha / 2)
    z_b = _normal_ppf(power)
    n = ((z_a + z_b) * sd_delta / mde) ** 2
    return max(2, int(math.ceil(n)))


def detectable_effect(
    sd_delta: float, n: int, *, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Minimum detectable paired delta given ``n`` reps. Inverse of the above."""
    if n < 2:
        return float("inf")
    z_a = _normal_ppf(1 - alpha / 2)
    z_b = _normal_ppf(power)
    return (z_a + z_b) * sd_delta / math.sqrt(n)


# ---------------------------------------------------------------------------
# Backends (scipy when present, stdlib fallback)
# ---------------------------------------------------------------------------


def _t_ppf(p: float, df: int) -> float:
    if _HAVE_SCIPY:
        return float(_scipy_stats.t.ppf(p, df))
    # Cornish-Fisher-ish fallback: normal approx with a small df inflation.
    z = _normal_ppf(p)
    g1 = (z**3 + z) / 4.0
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96.0
    return z + g1 / df + g2 / (df * df)


def _normal_ppf(p: float) -> float:
    if _HAVE_SCIPY:
        return float(_scipy_stats.norm.ppf(p))
    # Acklam's rational approximation for the inverse normal CDF.
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def _paired_t_pvalue(a: list[float], b: list[float]) -> float:
    deltas = [float(bi) - float(ai) for ai, bi in zip(a, b)]
    n = len(deltas)
    if n < 2:
        return 1.0
    if _HAVE_SCIPY:
        try:
            return float(_scipy_stats.ttest_rel(b, a).pvalue)
        except Exception:  # pragma: no cover
            pass
    mean = statistics.fmean(deltas)
    sd = statistics.stdev(deltas)
    if sd == 0:
        return 0.0 if mean != 0 else 1.0
    t = mean / (sd / math.sqrt(n))
    # two-sided p via normal approx on the fallback path
    return 2 * (1 - _normal_cdf(abs(t)))


def _wilcoxon_pvalue(deltas: list[float]) -> float | None:
    nonzero = [d for d in deltas if d != 0]
    if len(nonzero) < 6:  # Wilcoxon is unreliable with very few pairs
        return None
    if _HAVE_SCIPY:
        try:
            return float(_scipy_stats.wilcoxon(nonzero).pvalue)
        except Exception:  # pragma: no cover
            return None
    return None  # no stdlib Wilcoxon; report None and rely on the paired-t


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
