# Eval report — agent tuning pass (2026-07-02)

Live evaluation of four generative functions after the fidelity/grounding fixes
(launch punch list P4–P8), run to (a) validate those changes and (b) re-decide the
prod model/prompt per function under the cost → quality → performance rule.

- **Command (per function):** `python -m quizzical_evals.cli run --live --reps 20
  --concurrency 12 --function <fn> --judges gemini/gemini-2.5-pro
  --results results/cells_<fn>_20260702.jsonl --report-out results/report_<fn>_20260702.md`
- **Judge:** `gemini/gemini-2.5-pro` (same judge model as the 2026-07-01 PBW run),
  with a **512-token thinking budget** and a 2500-token output cap — see "Judge fix".
- **n:** reps=20 per (variant × input); 960 gen cells total across the four functions.
- **Keys:** OpenAI + Gemini from Key Vault (`openai-api-key`, `gemini-api-key`).
- **Spend:** gen $1.65 + judge ≈ $6.25 → **≈ $7.90 total** (budget $15).
- Raw artifacts: `evals/results/cells_<fn>_20260702.jsonl` + `report_<fn>_20260702.md`
  (gitignored, reproducible).

## What changed before this run (inputs to the eval)

1. **Prompt fidelity (P7)** — `prompts_adapter.get_prompt_pair("…", "baseline")` now
   resolves exactly like production `PromptManager.get_prompt` (App-Config override
   from `appconfig.local.yaml` `llm.prompts` first, then `DEFAULT_PROMPTS`). The
   harness previously scored `DEFAULT_PROMPTS` only — text prod never sends for the
   overridden functions. A new `default` strategy exposes the bare code default so
   overrides can be A/B-tested against it (this A/B decided the QG/NQG outcome below).
2. **FORBIDDEN block restored (P6)** — the QG/NQG `llm.prompts` overrides had
   silently dropped the anti-self-referential "ABSOLUTELY FORBIDDEN" block; re-added
   verbatim so the eval measured overrides at their best. (Superseded same-day by the
   eval outcome: the overrides were removed entirely — the code default, which always
   had the block, wins. The block is now pinned on the production-*resolved* prompt by
   `tests/unit/agent/test_appconfig_prompts_forbidden.py`.)
3. **PBW grounding (P4)** — `draft_character_profiles` now feeds a 1-line canonical
   hint per name (`canonical_hint_block`) when the topic resolves in the canonical
   catalog; empty otherwise. The eval assembles the same contexts. Also fixed two
   eval-only input-fidelity bugs: the harness used to demand "EXACTLY 6 profiles"
   while listing 4–5 names (count defaulted to 6), and rendered the roster as a
   Python list repr instead of production's enumerated "1. Name" block.
4. **Blank-profile fix (P8)** — the graph treats a name-matched but EMPTY
   `profile_text` from the batch as MISSING, so the per-character `profile_writer`
   fallback regenerates it instead of shipping a blank outcome.
5. **NQG waste removed (P5)** — the ~40-phrase progress pool inlined into every
   adaptive call (never referenced by any prompt template) and the dead
   `q_out.progress_phrase` read are gone; deterministic `pick_progress_phrase` is the
   single source (which is what users effectively always saw).

## Judge fix (why this run judges correctly)

The first attempt returned **empty judge output for 100% of calls**:
`gemini-2.5-pro` burns 600–1000 hidden reasoning tokens before any visible text, and
the harness capped judge output at 600 tokens. Fixed by raising the judge cap to 2500
and passing an explicit `thinking_budget=512` for Gemini judges (verified live:
~430 reasoning tokens used, intact scores, ~60% cheaper than unconstrained CoT).
**Consequence:** cross-run comparisons to the 2026-07-01 PBW absolute scores are
indicative only (different judge thinking configuration); within-run paired
comparisons are unaffected.

## Results

Quality = mean judge agg (1–5) with 95% CI; $/1k = mean gen cost per 1k calls;
n = 100 cells/variant for PBW (5 inputs), 60 for the rest (3 inputs).

### `profile_batch_writer` (floor 4.0 — re-run: inputs changed by P4 grounding)

| variant | model | $/1k | p95 s | quality (CI) | valid | floor met |
|---|---|---|---|---|---|---|
| **prod_4o_mini** ⬅ kept | `gpt-4o-mini` | $0.68 | 21.7 | **2.20 [2.09, 2.32]** | **100%** | NO |
| flash_latest_was | `gemini/gemini-flash-latest` | $7.39 | 19.3 | 2.07 [1.99, 2.17] | 94% | NO |
| flash_lite_cheapest | `gemini/gemini-2.5-flash-lite` | $0.36 | 19.6 | 1.77 [1.57, 1.96] | 56% | NO |

Paired (BH-corrected): vs flash-latest Δ+0.11 [-0.04,+0.25] p=0.15 (ns); vs flash-lite
Δ+0.39 p=0.004 (**sig**). **Decision: KEEP `gpt-4o-mini`** — cost/quality/validity
winner. The eval's strict coverage check (name present AND non-empty) reads 79% for
gpt-4o-mini; production backfills exactly those via the `profile_writer` fallback plus
the new P8 blank-profile-as-missing guard, so end-user coverage stays complete.
**Honest gap:** no model approaches the 4.0 floor (2.20 best). The canonical hints
apply to only 1 of 5 eval topics (the others are open/media/serious); the ceiling is
content depth, not model selection. Next levers: a few-shot exemplar of an excellent
differentiated profile, and retrieval-grounded contexts for media topics.

### `final_profile_writer` (floor 4.2 — first-ever live validation)

| variant | model | $/1k | p95 s | quality (CI) | valid | floor met |
|---|---|---|---|---|---|---|
| **prod_4o_mini** ⬅ kept | `gpt-4o-mini` | $0.35 | 9.6 | **4.68 [4.57, 4.80]** | **100%** | **YES (only one)** |
| gpt5_mini_quality | `gpt-5-mini` | $1.82 | 21.3 | 4.85 [4.68, 4.98] | 68% | rejected (validity) |
| flash_latest_was | `gemini/gemini-flash-latest` | $0.13 | — | 4.50 (n=2) | 3% | rejected (validity) |

**Decision: CONFIRM `gpt-4o-mini`** — the only variant clearing the strictest floor
in the suite, at 100% validity and the lowest reliable cost. gpt-5-mini's +0.10 point
estimate is not significant (p=0.21) and it ships usable output only 68% of the time
(reasoning-token truncation). flash-latest reproduces the R13 finding (3% valid,
empty-output starvation). The R13 pin is now evidence-backed, not just perf-inferred.

### `question_generator` (floor 4.0)

| variant | model | prompt | $/1k | p95 s | quality (CI) | valid | floor met |
|---|---|---|---|---|---|---|---|
| prod_4o_mini (shipped override) | `gpt-4o-mini` | App-Config CoT | $0.35 | 6.8 | 3.41 [3.27, 3.55] | 100% | NO |
| **4o_mini_default_prompt** ⬅ applied | `gpt-4o-mini` | code default | $0.35 | 12.0 | **3.66 [3.55, 3.77]** | 100% | NO |
| flash_latest_was | `gemini/gemini-flash-latest` | code-default-equiv | $6.03 | 14.4 | **4.97 [4.92, 5.01]** | 100% | **YES** |
| flash_lite_cheapest | `gemini/gemini-2.5-flash-lite` | baseline | $0.30 | 12.3 | 4.21 [4.02, 4.41] | 82% | rejected (validity) |

The shipped CoT-styled override is a **net quality negative** for gpt-4o-mini:
code default beats it Δ+0.25 [+0.10,+0.40], paired-t p=0.0023 (**sig**), at equal
cost and within the 18s latency budget. **Decision: KEEP `gpt-4o-mini`, REMOVE the
App-Config prompt override** (falls back to the code default, which carries the
FORBIDDEN block). **Honest gap:** no gpt-4o-mini variant clears the 4.0 floor; only
flash-latest does (4.97) at **17× the cost** on the user-blocking critical path that
prod deliberately moved OFF in R12 for live-pipeline empty-output/latency failures.
Per the owner's cost-first priority we keep the Pareto-best cheap/fast/100%-valid
config and record flash-latest as the known quality option if the floor is ever
promoted above cost. Follow-up: prompt work (few-shot exemplar of a high-information
baseline batch) is the cheapest path toward the floor.

### `next_question_generator` (floor 4.0 — the #1 loop hotspot, 6–12 calls/quiz)

| variant | model | prompt | $/1k | p95 s | quality (CI) | valid | floor met |
|---|---|---|---|---|---|---|---|
| prod_4o_mini (shipped override) | `gpt-4o-mini` | App-Config CoT | $0.17 | 6.6 | 4.36 [4.22, 4.50] | 100% | YES |
| **4o_mini_default_prompt** ⬅ applied | `gpt-4o-mini` | code default | **$0.15** | **2.4** | **4.45 [4.35, 4.55]** | 100% | **YES** |
| flash_latest_was | `gemini/gemini-flash-latest` | code-default-equiv | $3.04 | 7.1 | 4.77 [4.69, 4.84] | 100% | YES |
| flash_lite_cheap | `gemini/gemini-2.5-flash-lite` | baseline | $0.07 | 7.9 | 4.59 | 58% | rejected (validity) |

**Decision: KEEP `gpt-4o-mini`, REMOVE the App-Config prompt override.** The code
default wins on **all three axes simultaneously** on the hottest per-question loop:
-12% cost, p95 2.4s vs 6.6s (2.75× faster), quality 4.45 vs 4.36 — and clears the
floor (CI-lower 4.35 ≥ 4.0). Combined with the P5 pool-inlining removal this is a
straight cost+latency win per question served.

## Decisions applied (this branch)

1. `backend/appconfig.local.yaml` `llm.prompts`: **removed** the `question_generator`
   and `next_question_generator` overrides (both now resolve to `DEFAULT_PROMPTS`).
   `initial_planner`'s override is untouched (not re-evaluated this round — its
   inputs didn't change; the planner override carries extra dimension-vs-character
   guidance that the 2026-06-29 run validated).
2. `llm.tools`: models unchanged (all four stay `gpt-4o-mini`) — comments updated
   with AC-EVAL-2026-07-02 rationale + numbers per function.
3. Regression pins: `tests/unit/agent/test_appconfig_models_round14.py` (models +
   override removal) and `test_appconfig_prompts_forbidden.py` (FORBIDDEN block
   asserted on the production-RESOLVED prompt, robust to future overrides).

## Skipped as unchanged (deliberately not re-run)

- `initial_planner` — live reps=30 eval done 2026-06-29; no input/prompt changes in
  this pass affect it.
- `decision_maker` — checks-based eval done previously; no input changes.
- `profile_writer` (single) — shares the model pin with PBW's fallback path; its
  coverage role is exercised by construction (P8 guard test).

## Reproduce

```bash
cd evals
OPENAI_API_KEY=… GEMINI_API_KEY=… ../backend/.venv312/Scripts/python -m quizzical_evals.cli \
  run --live --reps 20 --concurrency 12 --function <fn> --judges gemini/gemini-2.5-pro \
  --results results/cells_<fn>_20260702.jsonl --report-out results/report_<fn>_20260702.md
```
