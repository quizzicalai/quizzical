# Quizzical Agent Eval -- Results
_How to read: cost is mean USD per call (per-1k shown for legibility); p50/p95 are wall-clock latency percentiles; quality is the judge agg (1-5) with a 95% bootstrap CI. A variant wins only if its quality **CI lower bound** clears the floor, then cost-first selection applies._

## `profile_batch_writer`

| variant | model | strategy | n | $/1k calls | p50 s | p95 s | quality (CI) | valid | checks |
|---|---|---|---|---|---|---|---|---|---|
| `flash_lite_cheapest` | `gemini/gemini-2.5-flash-lite` | baseline | 50 | $0.599 | 8.5 | 11.7 | 2.14 [2.06, 2.24] | 100% | profiles_cover_all_names=100%, short_desc_within_cap=100% |
| `4o_mini_cheaper` ⬅ winner | `gpt-4o-mini` | baseline | 50 | $0.613 | 11.1 | 20.1 | 2.94 [2.86, 3.00] | 100% | profiles_cover_all_names=0%, short_desc_within_cap=100% |
| `prod_flash_latest` (incumbent) | `gemini/gemini-flash-latest` | baseline | 50 | $8.250 | 17.0 | 18.9 | 2.60 [2.46, 2.74] | 100% | profiles_cover_all_names=100%, short_desc_within_cap=100% |

**Quality vs incumbent (`prod_flash_latest`), paired, Benjamini-Hochberg corrected:**

| variant | Δ quality | 95% CI on Δ | paired-t p | Wilcoxon p | sig? |
|---|---|---|---|---|---|
| `4o_mini_cheaper` | +0.34 | [+0.20, +0.48] | 0.000 | n/a | **yes** |
| `flash_lite_cheapest` | -0.46 | [-0.62, -0.30] | 0.000 | n/a | **yes** |

**Pareto frontier** (non-dominated on cost↓ / p95↓ / quality↑): `4o_mini_cheaper`, `flash_lite_cheapest`, `prod_flash_latest`

**Decision ⚠️ FLOOR NOT MET:** NO variant met the quality floor (CI-lower >= 4.00). Falling back to highest mean quality (4o_mini_cheaper); do NOT ship until the floor is met or explicitly lowered.

_Rejected:_ `prod_flash_latest` (quality CI-lower 2.46 < floor 4.00 (mean 2.60)); `4o_mini_cheaper` (check 'profiles_cover_all_names' pass-rate 0% < 98%); `flash_lite_cheapest` (quality CI-lower 2.06 < floor 4.00 (mean 2.14))

---
## Recommended configuration (per function)

| function | model | strategy | $/1k | p95 s | quality | floor met |
|---|---|---|---|---|---|---|
| `profile_batch_writer` | `gpt-4o-mini` | baseline | $0.613 | 20.1 | 2.94 | NO |

_Per-call cost of the winning config summed across functions: ~$0.61/1k calls. Multiply by the per-quiz call counts (see methodology) for a $/quiz estimate._
