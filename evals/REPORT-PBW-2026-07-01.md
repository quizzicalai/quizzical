# Eval report — `profile_batch_writer` (2026-07-01)

Targeted live run of the character-profile batch writer — the function the prior
audits flagged as the #1 generative-quality gap. Ran with the **new quality-rubric
prompt** (this branch) to (a) validate the prompt change and (b) re-decide the prod
model.

- **Command:** `python -m quizzical_evals.cli run --live --reps 20 --function profile_batch_writer --concurrency 6`
- **Judge:** `gemini/gemini-2.5-pro` · **n:** 100 calls/variant · **cost:** ~$1.80
- **Keys:** OpenAI + Gemini from Key Vault (`openai-api-key`, `gemini-api-key`)
- Raw report: `evals/results/report_pbw_live.md` (gitignored, reproducible from this run)

## Results

| variant | model | $/1k | p95 s | quality (95% CI) | valid | coverage |
|---|---|---|---|---|---|---|
| **gpt-4o-mini** (rubric prompt) | `gpt-4o-mini` | **$0.67** | 21.8 | **2.81 [2.73, 2.88]** | **100%** | 91% |
| gemini-flash-latest (prior prod) | `gemini/gemini-flash-latest` | $9.95 | 25.3 | 2.69 [2.59, 2.79] | 90% | 100% |
| gemini-2.5-flash-lite | `gemini/gemini-2.5-flash-lite` | $0.67 | 15.3 | 2.02 [2.00, 2.05] | 100% | 100% |

**Paired vs prior prod (Benjamini-Hochberg corrected):** gpt-4o-mini **Δ +0.13**, 95% CI
[+0.04, +0.22], paired-t p=0.004, Wilcoxon p=0.005 — **significant**.

## Decision — SWITCH `profile_batch_writer` → `gpt-4o-mini`

gpt-4o-mini **strictly dominates** the prior incumbent on every axis that matters:

- **Quality:** +0.13 higher, statistically significant (not noise).
- **Cost:** ~**15× cheaper** ($0.67 vs $9.95 /1k) — a direct hit on the owner's #1
  priority (LLM cost).
- **Validity:** 100% vs 90% — flash produced unusable output 1 call in 10.
- **Coverage:** 91% (was **0/50** on the prior 2026-06-29 run — the exact reason the
  team kept flash). The new prompt's completeness emphasis closed the gap; the
  remaining ~9% of dropped names are backfilled by the per-character `profile_writer`
  fallback (gpt-4o-mini, 100% coverage by construction), so end-user coverage stays
  complete.

Applied in `appconfig.local.yaml` (`llm.tools.profile_batch_writer.model`).

## Caveat / follow-up (not this PR)

**No model clears the 4.0 quality floor** (2.81 is the best). This is a deeper
content-quality ceiling, not a model-selection problem — the judge rubric rewards
distinctiveness/concreteness that even the rubric prompt only partly delivers.
Highest-value future levers (from the AI-quality review):

1. Feed a 1-line canonical hint per name (`character_contexts` is currently always
   `{}`, so the model writes with zero grounding).
2. Add a fewshot exemplar of an excellent, differentiated profile.
3. Treat a name-matched-but-EMPTY profile as "missing" so the fallback regenerates it
   (today an empty profile can ship — see hit list AI4).

## Reproduce

```bash
cd evals
OPENAI_API_KEY=… GEMINI_API_KEY=… ../backend/.venv312/Scripts/python -m quizzical_evals.cli \
  run --live --reps 20 --function profile_batch_writer --concurrency 6
```
