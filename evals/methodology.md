# Quizzical Agent Evaluation — Methodology

**Status:** framework v0.1 (2026-06-29). Authoring/scaffolding only — no paid LLM
calls have been run. All numbers in `REPORT-2026-06-29.md` are ILLUSTRATIVE
until a `--live` run populates them.

**Goal.** Let us optimize the LangGraph agent's per-function LLM calls in the
product's stated priority order — **cost → speed → quality** (ideally all
three) — and do so *defensibly* despite LLM output being non-deterministic. The
output of the framework is, per agent function, a recommended
`(model, prompt, knobs)` config with quantified cost, latency, quality, and a
significance-tested justification.

This directly closes the gap the 2026-06-28 launch audit flagged (finding #5):
> "the eval harness has zero token/cost accounting so the team literally cannot
> measure or optimize spend."

---

## 1. What we evaluate (the agent functions)

The agent (`backend/app/agent/graph.py`) is a sequence of structured LLM calls,
each routed by `tool_name` through `llm_helpers.invoke_structured` →
`llm_service.get_structured_response`. Each `tool_name` maps to a prompt
(`agent/prompts.py`) and a response schema (`agent/schemas.py`). We evaluate the
functions that materially shape the user-visible artifact and/or dominate cost:

| function (`tool_name`) | role | prod model (appconfig.local) | out-token cap | calls / quiz |
|---|---|---|---|---|
| `initial_planner` | title + synopsis + archetype roster | `gemini/gemini-flash-latest` | 2000 | 1 |
| `profile_batch_writer` | all character profiles in one call | `gemini/gemini-flash-latest` | 6000 | 0–1 |
| `profile_writer` | one character profile (fallback / >6 archetypes) | `gpt-4o-mini` | 800 | 0–N |
| `question_generator` | baseline question batch (user-blocking) | `gpt-4o-mini` | 2500 | 1 |
| `next_question_generator` | adaptive next question (loop hotspot) | `gpt-4o-mini` | 1500 | 6–12 |
| `decision_maker` | continue-or-finish + winner (loop hotspot) | `gpt-4o-mini` | 500 | 6–12 |
| `final_profile_writer` | the personalized reading (highest-stakes UX) | `gemini/gemini-flash-latest` | 1500 | 1 |

> The deterministic topic analysis (`analyze_topic` / intent classification) is
> **not** evaluated here — it makes no LLM call (it is keyword/config-driven), so
> it has no cost/quality variance to optimize. Likewise `synopsis_generator`,
> `character_list_generator`, `safety_checker`, `error_analyzer`,
> `failure_explainer`, and `image_prompt_enhancer` are wired in the config
> registry but not in the v0.1 sweep; add a `config/<tool>.yaml` to include them.

**Per-quiz call profile** (used to convert per-call cost → per-quiz cost): one
`initial_planner`, one `profile_batch_writer` (or N `profile_writer`), one
`question_generator`, then a loop of `decision_maker` + `next_question_generator`
(6–12 iterations), then one `final_profile_writer`. The loop functions therefore
dominate both spend and tail latency, which is why production already tuned them
first (AC-PROD-R11) — the framework lets us verify and extend that.

### Precompute promotion/safety judges

The precompute pipeline (`backend/app/services/precompute/`) already implements a
**two-judge consensus evaluator** (`evaluator.py`: take min score, union blocking
reasons; escalate to a higher tier when judges diverge >2 pts) and a **golden-set
promotion gate** (`golden.py`: a new evaluator may replace the incumbent only if
**both** precision **and** recall strictly improve). This framework does **not**
duplicate those; it **reuses their patterns** (consensus, strict-improvement gate)
for our judge ensemble and for the rule that a candidate config must beat the
incumbent on the lexicographic objective, not merely tie.

---

## 2. Metrics (per function × config)

### 2.1 Cost — **priority 1**

For every call we capture `Usage(prompt_tokens, completion_tokens,
reasoning_tokens)` from the provider response and price it with
`pricing.estimate_cost`. Prices come from a **version-pinned snapshot**
(`pricing.PRICE_OVERRIDES`, captured 2026-06-29 from `litellm.cost_per_token` in
the repo venv) so a dated report is reproducible regardless of later LiteLLM
upgrades; unknown models fall through to live `litellm` pricing.

Snapshot, USD per 1M tokens (input / output):

| model | input | output |
|---|---|---|
| `gpt-4o-mini` | $0.150 | $0.600 |
| `gpt-5-mini` | $0.250 | $2.000 |
| `gemini/gemini-2.5-flash-lite` | $0.100 | $0.400 |
| `gemini/gemini-flash-latest` | $0.300 | $2.500 |
| `gemini/gemini-2.5-flash` | $0.300 | $2.500 |
| `gpt-4o-2024-11-20` (judge) | $2.500 | $10.000 |
| `gemini/gemini-2.5-pro` (judge) | $2.500 | $10.000 |

We report **mean cost/call with a Student-t 95% CI** (cost is continuous and
roughly log-normal; the t-interval on the mean is adequate and we also report
the cheapest variant directly). We separately track `reasoning_tokens` because
reasoning models (Gemini Flash via the Responses API) bill hidden chain-of-thought
as output — that is precisely why production saw `gemini-flash-latest` cost and
latency blow up on the per-question loop and swapped to `gpt-4o-mini`.

A **failed paid call still costs money**, so cost is recorded even when parsing
fails; a config that is cheap-but-flaky is penalised through the validity gate
(§4), not given a free pass.

### 2.2 Speed — **priority 2**

We record **wall-clock latency per call** and report **p50 and p95** (not just
the mean — tail latency is what users feel, and reasoning models have fat tails).
Where the live caller streams, `latency_ttft_s` (time-to-first-token) is also
captured for the user-blocking calls (`initial_planner`, `question_generator`).
Each function may set a `latency_budget_p95_s` (a hard gate in the decision rule).

### 2.3 Quality — **priority 3**

Quality is **task-specific per function** and measured two ways:

**(a) Deterministic checks** (`checks.py`) — cheap, objective, no LLM. They mirror
the hard guards the production code already enforces, so a config can't "win the
judge" while violating structure:

| function | checks |
|---|---|
| `initial_planner` | has synopsis; archetype count in `[min,max]`; reproduces canonical roster when known |
| `profile_batch_writer` | covers every requested name (verbatim) with non-empty profile; short_desc ≤ 240 chars |
| `question_generator` | ≥ requested count; 2..max options each; questions unique; options don't just echo outcome names |
| `next_question_generator` | options well-formed; question unique vs history; no outcome-name leak |
| `decision_maker` | valid action enum; confidence ∈ [0,1]; **non-empty winner when FINISH_NOW** (mirrors the strict no-silent-fallback policy in `graph._resolve_winning_character`) |
| `final_profile_writer` | ≥3 paragraphs **and** ≥400 chars (the production `_is_final_profile_substantive` gate) |

**(b) LLM-as-judge** (`judges.py`) — a calibrated 1–5 Likert rubric reused
verbatim from the existing study (`backend/Analysis/judge.py`) so scores are
comparable to the prior 108-run experiment. Each function owns a subset of the
five dimensions (`synopsis_quality`, `character_completeness`, `baseline_quality`,
`answer_option_quality`, `final_profile_quality`); the per-function judge agg is
the mean of its owned dimensions.

`decision_maker` has **no judge dimension** — its correctness is objective. For a
live run, label each dataset record with `expected_action` (the fixtures already
carry this) and score **decision accuracy** against that calibration set instead
of a Likert judge.

**Judge-bias controls** (per 2026 LLM-judge best practice — see References):
- **Fixed judge model** across all variants → it's a *relative* yardstick; a
  judge swap is treated as an eval-suite migration, not a config change.
- **No self-judging**: the default judge (`gpt-4o`) is a different family from the
  cost candidates (`gpt-4o-mini`, `gemini-flash`); `judges.assert_not_self_judge`
  warns if a candidate shares the judge's family (self-preference risk).
- **Temperature 0** for reproducible scoring.
- **Optional multi-judge ensemble** (pass `--judges gpt-4o-2024-11-20,gemini/gemini-2.5-pro`):
  scores are averaged across families to damp any single judge's systematic bias
  (mirrors the precompute two-judge consensus pattern).
- **Reference-guided**: canonical rosters are passed to the judge as ground truth.

---

## 3. Handling non-determinism

### 3.1 Replication

Each `(function × variant × input)` cell is run **N times** (`--reps`). The unit
of replication is the cell, so a small but diverse input set still yields a
well-estimated per-function metric. Reps use distinct seeds; live runs get
variance for free from sampling temperature.

### 3.2 Confidence intervals

- **Quality** (bounded 1–5, discrete, often skewed): **percentile bootstrap CI**,
  10,000 resamples, fixed seed — no distributional assumption. (Default; matches
  the existing `Analysis/run_experiment._bootstrap_ci` but with 10k iters and a
  paired-delta path.)
- **Cost & latency** (continuous): **Student-t CI** on the mean; latency is
  additionally summarised by **p50/p95** because the mean hides the tail.

10,000 bootstrap resamples and the 2.5/97.5 percentiles are the field-standard
choice (see References).

### 3.3 Paired comparisons + significance

All variants of a function run on the **same inputs in the same order**, so
comparisons are **paired**: we test the per-input **delta vector** (variant − incumbent),
which removes input-difficulty variance and is far more powerful than an unpaired
test. For each candidate vs the incumbent we report:
- mean Δ with a **bootstrap CI on the delta** (a directional win needs the CI to
  exclude 0),
- a **paired-t p-value**, and
- a **Wilcoxon signed-rank p-value** (non-parametric backstop; if the two
  disagree, the effect is fragile).

### 3.4 Multiple-comparison correction

Comparing *k* variants pairwise inflates false positives. We expose both:
- **Benjamini-Hochberg (FDR)** — higher power; used for the **exploratory** variant
  sweep where a controlled fraction of false leads is acceptable.
- **Holm-Bonferroni (FWER)** — conservative; used for the **final go/no-go** on the
  chosen config, where a false positive ships a regression.

### 3.5 Power / minimum detectable effect (choosing N)

`stats.min_reps_for_effect(sd_delta, mde)` returns the reps needed to detect a
mean paired delta `mde` at α=0.05, power=0.80, using the standard one-sample/paired
normal approximation
`n = ((z_{1-α/2} + z_{1-β})·σ_Δ / MDE)²`. The companion
`stats.detectable_effect(sd_delta, n)` gives the MDE for a fixed budget.

**Procedure:** run a small pilot (`--reps 5`) to estimate σ_Δ of the quality
delta, decide the smallest quality difference worth acting on (e.g. MDE = 0.15 on
the 1–5 scale), then size the full run. As a rule of thumb, with σ_Δ ≈ 0.4 (typical
for this rubric) and MDE = 0.15, `n ≈ 56` paired observations per comparison;
across ~3–6 inputs that's **~10–20 reps per input**. Loop hotspots
(`decision_maker`, `next_question_generator`) deserve the higher end because their
per-quiz multiplicity makes even small effects economically large.

---

## 4. Decision rule — cost-first lexicographic with guardrails

Implemented in `decision.py`. Naively "pick the cheapest" would ship a
broken-but-free config, so cost-first is **lexicographic with hard gates**:

**Step 0 — Eligibility (all must hold):**
1. `validity_rate ≥ min_validity` (default 95%) — it actually returns usable output.
2. every `required_check` passes at `≥ check_pass_rate` (default 98%).
3. quality **CI lower bound** `≥ quality_floor` — we gate on the lower bound, not
   the point mean, so we never crown a config whose quality edge is within noise.
4. `p95 latency ≤ latency_budget` (if set).

**Step 1 — Cost:** among eligible variants, take the cheapest mean $/call.
**Step 2 — Speed:** among variants within `cost_tie_pct` (default 10%) of the
cheapest, take the fastest p95.
**Step 3 — Quality:** break remaining ties by highest mean judge score.

If **no** variant clears the floor, the framework refuses to auto-pick: it surfaces
the highest-quality option **with a `FLOOR NOT MET` warning** and says *do not ship*.
This is the analogue of the precompute "strict improvement" gate — we never
silently downgrade.

**Pareto view.** We also compute the **Pareto frontier** over
(cost↓, p95↓, quality↑). The lexicographic pick is always one point on it; the
frontier lets a human override when, e.g., a large quality gain justifies a small
cost bump (the `final_profile_writer` "spend-up" case). The report prints both.

---

## 5. Representative datasets

Fixtures live in `evals/datasets/<function>.json`, version-controlled and tiny by
design (see §3.1 — the replication unit is the cell). They span the four topic
**buckets** the agent branches on, so each branch is exercised:

| bucket | example | why it's included |
|---|---|---|
| `canonical` | Hogwarts House, MBTI | fixed ground-truth roster → tests `*_matches_canonical`, ref-guided judging |
| `media` | Star Wars OT Characters | proper-name roster from world knowledge (no canonical list) |
| `open` | Type of Coffee Drink, Houseplant | pure-creativity branch; no ground truth |
| `serious` | Doctor Specialty | factual tone; the rubric penalises whimsy here |

Buckets and the canonical sets are taken from the existing study
(`backend/Analysis/topics.py`) so results stay comparable. Adaptive/decision/final
fixtures additionally carry a realistic partial `quiz_history` so the model must
do the real task (a novel narrowing question, a finish/continue decision, a
reading that references the user's answers).

---

## 6. How a live run populates real numbers

```bash
cd evals
# 0. sanity-check spend/time first (no calls made):
python -m quizzical_evals.cli plan --reps 30

# 1. pilot to estimate variance, then size N via stats.min_reps_for_effect:
OPENAI_API_KEY=… GEMINI_API_KEY=… \
  python -m quizzical_evals.cli run --live --reps 5

# 2. full run at the sized N (run under the BACKEND venv so production prompt
#    text from app.agent.prompts is used, not the built-in fallbacks):
python -m quizzical_evals.cli run --live --reps 20 --concurrency 6

# 3. rebuild the report any time without re-running:
python -m quizzical_evals.cli report
```

**Rough budget** for the v0.1 config (6 functions, ~3–6 inputs each, 4 variants
where applicable) at `--reps 30`: **~2,400 cells, ≈ $13** (generation + a strong
judge per cell), **~50–60 min** wall at `--concurrency 6` — see `cli.py plan` for
the live estimate. A `--reps 5` pilot is **~$2** and a few minutes.

---

## 7. Threats to validity (and mitigations)

- **Judge bias / drift** → fixed cross-family judge, temp 0, optional ensemble,
  reference-guided; recalibrate against human ratings periodically (precompute
  golden-set pattern).
- **Prompt drift between eval and prod** → prompts are *imported* from
  `app.agent.prompts` at runtime (not copied); the built-in fallbacks are used only
  when the backend isn't importable and the report is then labelled accordingly.
- **Price drift** → version-pinned price snapshot; re-snapshot and re-date the
  report when prices change.
- **Small-sample over-claiming** → gate on CI lower bound, require N via power
  analysis, correct for multiple comparisons (Holm for the final call).
- **Self-preference** → never judge a model with its own family; warn if attempted.
- **Mock ≠ reality** → any report built on the offline mock is explicitly tagged
  `ILLUSTRATIVE`; only a `--live` run produces decision-grade numbers.

---

## References (LLM-eval statistical best practice)

- A/B Testing LLM Prompts in 2026 — bootstrap CIs, paired analysis, sample sizing.
  <https://futureagi.com/blog/ab-testing-llm-prompts-best-practices-2026/>
- Statistical LLM Evaluations — Confidence scoring (bootstrap, CI excludes 0).
  <https://medium.com/@sulbha.jindal/statistical-llm-evaluations-confidence-scoring-caa6c9d57656>
- LLM-as-Judge Best Practices 2026 — calibration, bias, cost; judge-swap = migration.
  <https://futureagi.com/blog/llm-as-judge-best-practices-2026>
- LLM-Judge Bias Mitigation 2026 — position/verbosity/self-preference/format/calibration.
  <https://futureagi.com/blog/evaluating-llm-judge-bias-mitigation-2026/>
- Self-Preference Bias in LLM-as-a-Judge (arXiv:2410.21819).
  <https://arxiv.org/pdf/2410.21819>
- Benjamini & Hochberg (1995), FDR control; Holm (1979), step-down FWER — standard
  multiple-comparison corrections.
