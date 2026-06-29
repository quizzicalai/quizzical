"""Command-line entry point for the eval harness.

Examples
--------
    # Offline, free, deterministic. Runs the whole pipeline + builds a report.
    python -m quizzical_evals.cli run --dry-run --reps 8

    # Just one function:
    python -m quizzical_evals.cli run --dry-run --function next_question_generator

    # Live run (REAL paid calls). Requires OPENAI_API_KEY and/or GEMINI_API_KEY.
    python -m quizzical_evals.cli run --live --reps 30 --concurrency 6

    # Rebuild the report from an existing results JSONL without re-running:
    python -m quizzical_evals.cli report --results results/cells.jsonl

    # Estimate cost/time of a planned LIVE run before spending anything:
    python -m quizzical_evals.cli plan --reps 30

Run this from the ``evals/`` directory (so ``config/`` and ``datasets/`` resolve),
or pass absolute ``--config-dir`` / ``--results`` paths.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config_loader import load_all_specs, load_spec
from .datasets import load_dataset
from .judges import default_judge_model
from .report import build_function_report, is_illustrative, render_markdown
from .runner import run_all
from .schema import FunctionEvalSpec, load_results

_EVALS_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RESULTS = _EVALS_ROOT / "results" / "cells.jsonl"


def _select_specs(args) -> list[FunctionEvalSpec]:
    if args.function:
        path = _EVALS_ROOT / "config" / f"{args.function}.yaml"
        return [load_spec(path)]
    return load_all_specs(args.config_dir)


def _cmd_run(args) -> int:
    specs = _select_specs(args)
    if args.live:
        import os

        if not (os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")):
            print("ERROR: --live needs OPENAI_API_KEY and/or GEMINI_API_KEY", file=sys.stderr)
            return 2
        print("LIVE RUN: this will make paid LLM calls.")
    else:
        print("DRY RUN: offline mock, no paid calls, deterministic.")

    judge_models = tuple(j.strip() for j in args.judges.split(",")) if args.judges else None
    results = asyncio.run(
        run_all(
            specs,
            reps=args.reps,
            live=args.live,
            concurrency=args.concurrency,
            results_path=args.results,
            judge_models=judge_models,
        )
    )
    print(f"Wrote {len(results)} cells -> {args.results}")
    _write_report(specs, args.results, args.report_out)
    return 0


def _cmd_report(args) -> int:
    specs = _select_specs(args)
    _write_report(specs, args.results, args.report_out)
    return 0


def _cmd_plan(args) -> int:
    """Estimate the size, rough token volume, and $ of a planned LIVE run.

    Uses the mock token model as a coarse proxy for per-call tokens; the real
    figure depends on prompt sizes and model verbosity. This is a *budget sanity
    check*, not a guarantee.
    """
    from .pricing import Usage, estimate_cost

    specs = _select_specs(args)
    total_cells = 0
    total_cost = 0.0
    print(f"Planned run: reps={args.reps}, judge={default_judge_model()}\n")
    print(f"{'function':28s} {'variants':>8s} {'inputs':>7s} {'cells':>7s} {'~$ (gen+judge)':>16s}")
    for spec in specs:
        n_inputs = len(load_dataset(spec.dataset))
        cells = len(spec.variants) * n_inputs * args.reps
        # crude per-call proxy: cap-driven completion + ~600-token prompt
        spec_cost = 0.0
        for v in spec.variants:
            cap = v.max_output_tokens or 1500
            gen = estimate_cost(v.model, Usage(700, int(cap * 0.5), 0))
            judge = estimate_cost(default_judge_model(), Usage(900, 150, 0))
            spec_cost += (gen + judge) * n_inputs * args.reps
        total_cells += cells
        total_cost += spec_cost
        print(f"{spec.function:28s} {len(spec.variants):>8d} {n_inputs:>7d} {cells:>7d} {'$'+format(spec_cost,'.2f'):>16s}")
    print(f"\nTOTAL cells: {total_cells}   rough cost: ${total_cost:.2f}")
    print(
        "Rough wall time at concurrency C and ~4 s/call: "
        f"~{(total_cells*2)/max(1,args.concurrency)*4/60:.0f} min "
        "(x2 for gen+judge). Tune --concurrency to fit provider rate limits."
    )
    return 0


def _write_report(specs, results_path, report_out) -> None:
    rows = load_results(results_path)
    if not rows:
        print(f"No results at {results_path}; run first.", file=sys.stderr)
        return
    spec_by_fn = {s.function: s for s in specs}
    # include any function present in results even if not in selected specs
    functions = sorted({r.function for r in rows})
    reports = []
    for fn in functions:
        spec = spec_by_fn.get(fn)
        if spec is None:
            try:
                spec = load_spec(_EVALS_ROOT / "config" / f"{fn}.yaml")
            except Exception:
                continue
        reports.append(build_function_report(spec, [r for r in rows if r.function == fn]))
    md = render_markdown(
        reports,
        illustrative=is_illustrative(rows),
        title="Quizzical Agent Eval -- Results",
    )
    Path(report_out).write_text(md, encoding="utf-8")
    print(f"Wrote report -> {report_out}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quizzical_evals")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", default=str(_EVALS_ROOT / "config"))
    common.add_argument("--function", default=None, help="single function name")
    common.add_argument("--results", default=str(_DEFAULT_RESULTS))
    common.add_argument("--report-out", default=str(_EVALS_ROOT / "results" / "report.md"))

    r = sub.add_parser("run", parents=[common], help="run cells then build report")
    r.add_argument("--reps", type=int, default=8)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--live", action="store_true", help="make REAL paid calls")
    r.add_argument("--dry-run", action="store_true", help="offline mock (default)")
    r.add_argument("--judges", default=None, help="comma-separated judge model ids")
    r.set_defaults(func=_cmd_run)

    rep = sub.add_parser("report", parents=[common], help="rebuild report from JSONL")
    rep.set_defaults(func=_cmd_report)

    pl = sub.add_parser("plan", parents=[common], help="estimate cost/time of a live run")
    pl.add_argument("--reps", type=int, default=30)
    pl.add_argument("--concurrency", type=int, default=6)
    pl.set_defaults(func=_cmd_plan)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # --live overrides --dry-run; default is dry-run.
    if getattr(args, "live", False) and getattr(args, "dry_run", False):
        print("Both --live and --dry-run given; --live wins.", file=sys.stderr)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
