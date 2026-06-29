# `evals/` — Quizzical agent evaluation framework

A statistically grounded harness for optimizing the LangGraph agent's per-function
LLM calls in the priority order **cost → speed → quality**, despite
non-deterministic LLM output.

- **Methodology:** [`methodology.md`](methodology.md) — metrics, CIs, significance
  testing, multiple-comparison correction, power analysis, the decision rule.
- **Example report:** [`REPORT-2026-06-29.md`](REPORT-2026-06-29.md) — presentation-
  quality, ILLUSTRATIVE until a live run.

## Quickstart

```bash
cd evals
pip install -r requirements.txt          # numpy/scipy already in the backend venv

# Offline, free, deterministic — runs the full pipeline + builds a report:
python -m quizzical_evals.cli run --dry-run --reps 8

# Estimate the cost/time of a LIVE run before spending anything:
python -m quizzical_evals.cli plan --reps 30

# Live run (REAL paid calls; needs OPENAI_API_KEY and/or GEMINI_API_KEY).
# Run under the BACKEND venv so production prompt text is used:
python -m quizzical_evals.cli run --live --reps 20 --concurrency 6

# Rebuild the report from an existing results file without re-running:
python -m quizzical_evals.cli report
```

## Layout

```
evals/
  README.md                     this file
  methodology.md                the rigorous design doc
  REPORT-2026-06-29.md          polished, presentation-quality report (ILLUSTRATIVE)
  requirements.txt              eval-only deps (numpy, scipy, pyyaml, litellm)
  config/                       one YAML per agent function = the variant sweep
    initial_planner.yaml
    profile_batch_writer.yaml
    question_generator.yaml
    next_question_generator.yaml
    decision_maker.yaml
    final_profile_writer.yaml
  datasets/                     small, version-controlled input fixtures (per function)
    *.json
  results/                      run artifacts (git-ignored): cells.jsonl, report.md
  quizzical_evals/              the package
    pricing.py                  token usage -> USD (version-pinned snapshot + litellm)
    schema.py                   config (FunctionEvalSpec/ConfigVariant) + result (CellResult)
    config_loader.py            load config/*.yaml -> FunctionEvalSpec
    datasets.py                 load fixtures + assemble prompt context
    prompts_adapter.py          import REAL prompts from app.agent.prompts (fallback if absent)
    caller.py                   LLM caller: MockCaller (offline) | LiveCaller (litellm)
    parse.py                    robust JSON extraction (mirrors llm_service)
    checks.py                   deterministic, code-only quality gates per function
    judges.py                   LLM-as-judge (calibrated rubric, bias controls)
    runner.py                   execute (variant x input x rep) cells -> cells.jsonl
    stats.py                    CIs, paired tests, Holm/BH correction, power/MDE
    decision.py                 cost->speed->quality lexicographic rule + Pareto
    report.py                   aggregate -> markdown
    cli.py                      `run` / `report` / `plan`
```

## How it works (one paragraph)

For each agent function, a `config/<function>.yaml` lists the `(model, prompt,
knob)` variants to compare and the function's quality floor + latency budget. The
runner renders the **real production prompt** (imported from `app.agent.prompts`)
for each variant × dataset-input × repeat, calls the model, and records tokens→\$,
wall latency, deterministic checks, and an LLM-judge quality score into
`results/cells.jsonl`. The report module rolls those up into per-variant cost CIs,
latency p50/p95, bootstrap quality CIs, validity/check pass-rates, paired
significance vs the incumbent (Benjamini-Hochberg / Holm corrected), a Pareto
frontier, and a cost→speed→quality winner — then writes markdown.

## Design guarantees

- **No paid calls without `--live`.** Default `--dry-run` is deterministic and free.
- **Reproducible cost.** Prices come from a dated snapshot in `pricing.PRICE_OVERRIDES`.
- **No silent downgrades.** A variant wins only if its quality **CI lower bound**
  clears the floor and it passes the structural checks; otherwise the framework
  refuses to auto-pick and emits a `FLOOR NOT MET` warning.
- **Reuses, doesn't duplicate** `backend/Analysis/` (judge rubric, prompt
  strategies, topics) and `backend/app/services/precompute/` (consensus +
  strict-improvement gate patterns).

## Adding a function or variant

1. Add/extend `config/<tool>.yaml` (a `function`, its `dataset`, `judge_dimensions`,
   `deterministic_checks`, `quality_floor`, optional `latency_budget_p95_s`, and the
   `variants` list).
2. Add `datasets/<tool>.json` (a list of input records — see existing files for the
   shape and per-bucket rationale).
3. If the function needs a new objective gate, add a check to `checks.py` and
   reference it by name. The runner/stats/report handle any function generically.
