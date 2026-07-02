"""Aggregate CellResults -> per-function tables, decisions, and a markdown report.

Pipeline:
    CellResults (JSONL)
      -> group by (function, variant)
      -> VariantAggregate (cost CI, latency p50/p95, quality CI, validity, checks)
      -> per-function pairwise significance vs the incumbent (with BH correction)
      -> Decision (cost -> speed -> quality lexicographic + Pareto frontier)
      -> markdown

The markdown is intentionally presentation-quality and self-explanatory: every
table says how to read its CIs and significance markers, and each function ends
with a one-paragraph decision walkthrough.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .decision import Decision, VariantAggregate, decide
from .schema import CellResult, FunctionEvalSpec
from .stats import (
    Estimate,
    benjamini_hochberg,
    bootstrap_ci,
    paired_compare,
    percentile,
    t_interval,
)


@dataclass
class FunctionReport:
    function: str
    aggregates: list[VariantAggregate]
    decision: Decision
    incumbent: str | None
    comparisons: list[dict]  # vs incumbent, BH-corrected


def _aggregate_variant(
    function: str, variant: str, rows: list[CellResult]
) -> VariantAggregate:
    model = rows[0].model
    strat = rows[0].prompt_strategy
    n = len(rows)
    valid = [r for r in rows if r.valid_output and r.error is None]
    validity_rate = len(valid) / n if n else 0.0

    costs = [r.cost_usd for r in rows]  # cost counts even on failure
    lats = [r.latency_wall_s for r in rows]
    quals = [r.judge_agg for r in valid if r.judge_agg is not None]

    cost_est = t_interval(costs) if costs else Estimate(0, 0, 0, 0, "t-interval")
    quality_est = (
        bootstrap_ci(quals) if quals else Estimate(0.0, 0.0, 0.0, 0, "bootstrap-mean")
    )

    # Check pass-rates across valid rows.
    check_names = set()
    for r in valid:
        check_names.update(r.check_results.keys())
    check_rates = {
        c: (
            sum(1 for r in valid if r.check_results.get(c)) / len(valid)
            if valid
            else 0.0
        )
        for c in check_names
    }

    return VariantAggregate(
        variant=variant,
        model=model,
        prompt_strategy=strat,
        n=n,
        cost_usd=cost_est,
        latency_p50_s=percentile(lats, 50),
        latency_p95_s=percentile(lats, 95),
        quality=quality_est,
        validity_rate=validity_rate,
        check_pass_rates=check_rates,
    )


def _paired_quality_vectors(
    a_rows: list[CellResult], b_rows: list[CellResult]
) -> tuple[list[float], list[float]]:
    """Align two variants by (input_id, rep) so deltas are truly paired."""

    def key(r: CellResult) -> tuple[str, int]:
        return (r.input_id, r.rep)

    a_map = {key(r): r.judge_agg for r in a_rows if r.judge_agg is not None}
    b_map = {key(r): r.judge_agg for r in b_rows if r.judge_agg is not None}
    common = sorted(set(a_map) & set(b_map))
    return [a_map[k] for k in common], [b_map[k] for k in common]


def build_function_report(
    spec: FunctionEvalSpec, rows: list[CellResult]
) -> FunctionReport:
    by_variant: dict[str, list[CellResult]] = defaultdict(list)
    for r in rows:
        if r.function == spec.function:
            by_variant[r.variant].append(r)

    aggregates = [_aggregate_variant(spec.function, v, rs) for v, rs in by_variant.items()]

    # Incumbent = the variant flagged as production in config (name contains
    # "prod") or, failing that, the first variant.
    incumbent = next(
        (a.variant for a in aggregates if "prod" in a.variant.lower()),
        aggregates[0].variant if aggregates else None,
    )

    # Pairwise quality significance vs incumbent (BH-corrected across variants).
    comparisons: list[dict] = []
    if incumbent:
        inc_rows = by_variant[incumbent]
        pvals: list[float] = []
        raw: list[dict] = []
        for a in aggregates:
            if a.variant == incumbent:
                continue
            av, bv = _paired_quality_vectors(inc_rows, by_variant[a.variant])
            if len(av) >= 2:
                pt = paired_compare(av, bv)
                raw.append(
                    {
                        "variant": a.variant,
                        "mean_delta": pt.mean_delta,
                        "ci_lo": pt.ci_lo,
                        "ci_hi": pt.ci_hi,
                        "t_p": pt.t_pvalue,
                        "wilcoxon_p": pt.wilcoxon_pvalue,
                        "n_pairs": pt.n_pairs,
                    }
                )
                pvals.append(pt.t_pvalue)
        reject = benjamini_hochberg(pvals) if pvals else []
        for i, c in enumerate(raw):
            c["significant_bh"] = reject[i] if i < len(reject) else False
        comparisons = raw

    decision = decide(
        spec.function,
        aggregates,
        quality_floor=spec.quality_floor.min_mean_score,
        required_checks=spec.quality_floor.required_checks,
        latency_budget_p95_s=spec.latency_budget_p95_s,
    )
    return FunctionReport(
        function=spec.function,
        aggregates=aggregates,
        decision=decision,
        incumbent=incumbent,
        comparisons=comparisons,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(
    reports: list[FunctionReport], *, illustrative: bool, title: str
) -> str:
    out: list[str] = []
    out.append(f"# {title}\n")
    if illustrative:
        out.append(
            "> **ILLUSTRATIVE** -- generated from the offline mock harness "
            "(`--dry-run`). Numbers are synthetic placeholders. Re-run with "
            "`--live` and provider keys to populate real cost/latency/quality.\n"
        )
    out.append(
        "_How to read: cost is mean USD per call (per-1k shown for legibility); "
        "p50/p95 are wall-clock latency percentiles; quality is the judge agg "
        "(1-5) with a 95% bootstrap CI. A variant wins only if its quality **CI "
        "lower bound** clears the floor, then cost-first selection applies._\n"
    )

    for fr in reports:
        out.append(f"\n## `{fr.function}`\n")
        _render_variant_table(out, fr)
        _render_significance(out, fr)
        _render_pareto(out, fr)
        _render_decision(out, fr)

    out.append("\n---\n")
    out.append(_render_overall(reports))
    return "".join(out)


def _fmt_ci(e: Estimate) -> str:
    return f"[{e.lo:.2f}, {e.hi:.2f}]"


def _render_variant_table(out: list[str], fr: FunctionReport) -> None:
    out.append(
        "\n| variant | model | strategy | n | $/1k calls | p50 s | p95 s | "
        "quality (CI) | valid | checks |\n"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|\n")
    ranked = sorted(fr.aggregates, key=lambda a: a.cost_usd.mean)
    for a in ranked:
        checks = (
            ", ".join(f"{k}={v:.0%}" for k, v in sorted(a.check_pass_rates.items()))
            or "-"
        )
        mark = " ⬅ winner" if fr.decision.winner and a.variant == fr.decision.winner.variant else ""
        inc = " (incumbent)" if a.variant == fr.incumbent else ""
        out.append(
            f"| `{a.variant}`{inc}{mark} | `{a.model}` | {a.prompt_strategy} | {a.n} | "
            f"${a.cost_usd.mean*1000:.3f} | {a.latency_p50_s:.1f} | {a.latency_p95_s:.1f} | "
            f"{a.quality.mean:.2f} {_fmt_ci(a.quality)} | {a.validity_rate:.0%} | {checks} |\n"
        )


def _render_significance(out: list[str], fr: FunctionReport) -> None:
    if not fr.comparisons:
        return
    out.append(
        f"\n**Quality vs incumbent (`{fr.incumbent}`), paired, "
        "Benjamini-Hochberg corrected:**\n\n"
    )
    out.append("| variant | Δ quality | 95% CI on Δ | paired-t p | Wilcoxon p | sig? |\n")
    out.append("|---|---|---|---|---|---|\n")
    for c in fr.comparisons:
        w = f"{c['wilcoxon_p']:.3f}" if c["wilcoxon_p"] is not None else "n/a"
        sig = "**yes**" if c["significant_bh"] else "no"
        out.append(
            f"| `{c['variant']}` | {c['mean_delta']:+.2f} | "
            f"[{c['ci_lo']:+.2f}, {c['ci_hi']:+.2f}] | {c['t_p']:.3f} | {w} | {sig} |\n"
        )


def _render_pareto(out: list[str], fr: FunctionReport) -> None:
    front = {a.variant for a in fr.decision.pareto_front}
    names = ", ".join(f"`{v}`" for v in sorted(front)) or "(none)"
    out.append(
        f"\n**Pareto frontier** (non-dominated on cost↓ / p95↓ / quality↑): {names}\n"
    )


def _render_decision(out: list[str], fr: FunctionReport) -> None:
    d: Decision = fr.decision
    flag = "" if d.floor_met else " ⚠️ FLOOR NOT MET"
    out.append(f"\n**Decision{flag}:** {d.rationale}\n")
    if d.rejected:
        out.append("\n_Rejected:_ ")
        out.append(
            "; ".join(f"`{v.variant}` ({reason})" for v, reason in d.rejected) + "\n"
        )


def _render_overall(reports: list[FunctionReport]) -> str:
    lines = ["## Recommended configuration (per function)\n\n"]
    lines.append("| function | model | strategy | $/1k | p95 s | quality | floor met |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    total_cost = 0.0
    for fr in reports:
        w = fr.decision.winner
        if not w:
            lines.append(f"| `{fr.function}` | (no data) | | | | | |\n")
            continue
        total_cost += w.cost_usd.mean
        lines.append(
            f"| `{fr.function}` | `{w.model}` | {w.prompt_strategy} | "
            f"${w.cost_usd.mean*1000:.3f} | {w.latency_p95_s:.1f} | {w.quality.mean:.2f} | "
            f"{'yes' if fr.decision.floor_met else 'NO'} |\n"
        )
    lines.append(
        f"\n_Per-call cost of the winning config summed across functions: "
        f"~${total_cost*1000:.2f}/1k calls. Multiply by the per-quiz call counts "
        f"(see methodology) for a $/quiz estimate._\n"
    )
    return "".join(lines)


def is_illustrative(rows: list[CellResult]) -> bool:
    return any(r.dry_run for r in rows)
