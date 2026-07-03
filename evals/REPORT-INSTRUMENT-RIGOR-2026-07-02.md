# Eval report — INSTRUMENT RIGOR A/B (2026-07-02)

Owner blackbox finding #5: "Serious topics like Myers-Briggs or DISC need more
scientific rigour in how it asks questions." This run measures the new
conditional INSTRUMENT RIGOR prompt block (branch `feat/instrument-rigor`)
BEFORE vs AFTER on **instrument topics only** (MBTI, DISC, Big Five), holding
the production model and knobs fixed on both arms.

- **Command:** `python -m quizzical_evals.cli run --live --config-dir config/rigor
  --reps 12 --concurrency 6 --judges gpt-4o-2024-11-20
  --results results/cells_rigor_gpt4o_20260702.jsonl`
- **Arms (per function):** `pre_rigor_4o_mini` (strategy `no_rigor` — the
  pre-change production prompt, verified **byte-identical** to origin/main's
  `DEFAULT_PROMPTS` after stripping the new hooks) vs `rigor_4o_mini`
  (strategy `default` — the new code default with `{instrument_rigor}` filled
  from the canonical-catalog dimensions, exactly as the tools do).
- **n:** 2 functions x 2 variants x 3 inputs x 12 reps = 144 gen cells,
  100% valid output on every arm.
- **Judge:** `gpt-4o-2024-11-20` (temp 0). **Caveat:** the preferred
  cross-family judge (`gemini/gemini-2.5-pro`, used for the 2026-07-02 tuning
  run) was unusable — the shared Gemini key returned project-quota 429s on
  ~100% of calls (first attempt `cells_rigor_20260702.jsonl` kept for the
  deterministic numbers). gpt-4o shares a family with the gpt-4o-mini
  candidate, but BOTH arms are gpt-4o-mini, so any family bias applies
  equally and the PAIRED delta remains meaningful. Absolute scores are not
  comparable to the Gemini-judged headline series (3.66 QG / 4.45 NQG).
- **Spend:** gen 2 x ~$0.04 + gpt-4o judge ~= $0.70 + partial Gemini-judged
  first run ~= $0.25 → **~= $1.05 total** (budget $8).

## Results (paired by input x rep)

### `question_generator` (baseline batch) — the win

| arm | quality (CI95) | instrument_rigor dim | dims valid | coverage balanced | p95 s | $/1k |
|---|---|---|---|---|---|---|
| BEFORE `no_rigor` | 3.74 [3.68, 3.81] | 3.31 | 0% | 0% | 8.1 | $0.38 |
| AFTER `rigor` | **4.26 [4.22, 4.31]** | **4.79** | **100%** | **100%** | 5.4 | $0.40 |

Paired delta (n=33 pairs judged on both arms): **judge_agg +0.49
[+0.41, +0.58], paired-t p < 0.0001**; instrument_rigor sub-score **+1.45
[+1.21, +1.70]**. The AFTER arm clears the 4.0 floor on instrument topics
(CI-lower 4.22); BEFORE does not. Every AFTER batch tagged all questions with
a valid dimension code AND spread them evenly (no dim missed, none over
ceil(n/k)). Cost +5%, latency actually improved.

### `next_question_generator` (adaptive)

| arm | quality (CI95) | instrument_rigor dim | dims valid | targets least-covered | p95 s | $/1k |
|---|---|---|---|---|---|---|
| BEFORE `no_rigor` | 3.93 [3.88, 3.97] | 3.78 | 0% | 0% | 3.7 | $0.16 |
| AFTER `rigor` | 3.86 [3.73, 3.99] | 3.81 | **100%** | **100%** | 2.0 | $0.24 |

Paired delta (n=36): judge_agg -0.06 [-0.19, +0.06], p=0.33 (**not
significant**); instrument_rigor +0.03 (ns). The judge scores a single
question in isolation and cannot see whole-quiz dimension balance, so the real
signal here is deterministic: **100% of AFTER questions probed a least-covered
dimension** (given the record's `asked_dimensions`), vs 0% steering capability
before. That is the property that makes a *finished* quiz cover E/I, S/N,
T/F, J/P instead of clustering. Cost +$0.08/1k calls (larger prompt); p95
nearly halved.

### Deterministic-only confirmation (Gemini-judge-starved run 1)

`cells_rigor_20260702.jsonl` (independent 144 cells, same arms): identical
check outcomes — QG dims-valid/coverage-balanced 0%→100%, NQG
targets-least-covered 0%→100%. The behavior is reproducible.

## Qualitative sample (MBTI, gpt-4o-mini, same input)

BEFORE (typical): "When faced with a new project, how do you typically
approach it?" with options mixing J/P, E/I and a signal-free third option
("I weigh the pros and cons before deciding.").

AFTER: 6 questions tagged E/I x2, S/N x2, T/F x1, J/P x1, each with exactly
pole-mapped options, e.g. `T/F` — "When faced with a difficult decision, you
are more likely to rely on..." → "Logical analysis and objective criteria." /
"Your personal values and how it affects others."

## Decision / follow-ups

- The rigor block **ships ON by default in code** (it is conditional on the
  topic resolving to a catalog instrument; every other topic renders "").
  No App-Config flag: the OFF path is the topic itself.
- Whimsy regression risk: none observed — the `no_rigor`-vs-`default` diff is
  empty for non-instrument topics by construction (placeholder renders "");
  the wiring tests pin this.
- Deferred: re-judge with a cross-family judge when the Gemini quota resets
  (paired delta expected to hold; absolute levels may shift); Enneagram /
  Holland instrument cells (dimensions are in the catalog but were not in
  this run's topic cells); a full-quiz simulation eval that measures
  end-to-end dimension coverage through the decide loop.
