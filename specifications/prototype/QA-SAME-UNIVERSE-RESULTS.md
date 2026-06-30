# Same-Universe Q&A Imagery — Implementation + Evaluation

**Branch:** `feat/qa-images-same-universe` (isolated clone)
**Date:** 2026-06-29
**Status:** Built, flag-gated **OFF** by default. Flag-off is byte-for-byte today's behaviour. No real FAL spend this round (no local FAL key) — validated with the production prompt builder + a fake FAL client + an estimated cost model.

This advances the owner's **same-universe** vision: topic/universe-specific imagery (e.g. *Harry Potter* → "Dumbledore looking into a pensieve"), **not** generic clipart. The merged generic-icon foundation (`quizzical.images.qa_icons_enabled`) stays the **fallback** for strings/topics the generation path skips.

---

## What was built (priority order)

### 1. FAL $-ledger — the hard prerequisite (was missing)
The cost-abuse hole prior reviews flagged. Neither the per-day LLM `cost_guard` nor the in-memory `scripts/_precompute_spend.SpendLedger` is a durable, lifetime, FAL-only cap.

- **`fal_spend_ledger` table** (ORM `FalSpendLedger` in `backend/app/models/db.py` + DDL in `backend/db/init/init.sql`): append-only, one row per FAL attempt — `purpose`, `topic_slug`, `prompt_hash`, `fal_request_url`, `cost_cents`, `status` (`charged|reused|blocked`), `created_at`. Persists across processes/deploys, so repeated builds can't overrun the budget.
- **`FalBudgetConfig`** under `settings.images.fal_budget`: `cap_usd=150.0` (owner budget), `cost_per_image_usd=0.011` (FLUX schnell, matches `_precompute_spend.COST_FAL_IMAGE_CENTS`), `enforce=True`.
- **`FalLedger`** (`backend/app/services/icons/fal_ledger.py`): `total_spent_cents()` / `snapshot()` (lifetime SUM), and **`guarded_generate()`** — the single seam that enforces the invariant: **no FAL call proceeds without a pre-flight cap check, and every attempt is recorded after.** When the cap would be breached it writes a `blocked` audit row and returns `None` (the pack then falls back to a generic icon / no image). The affordability check and the recorded charge use the *same* integer-cents value, so the cap can't over/under-block by a rounding delta.

**Cap proof (real `FalLedger` vs SQLite, 3¢ cap, fake client):**

| attempt | FAL called? | lifetime spend |
|--------:|:-----------:|---------------:|
| 1 | yes | $0.01 |
| 2 | yes | $0.02 |
| 3 | yes | $0.03 |
| 4 | **blocked** | $0.03 |
| 5 | **blocked** | $0.03 |

Spend never exceeds the cap; FAL is not called once exhausted.

### 2. Same-universe generation pipeline
- **`build_qa_image_prompt`** (`backend/app/agent/tools/image_tools.py`): a pure, hot-path prompt builder that places the **universe first** ("In the world of *Harry Potter*: …") and the Q&A string as the subject, then appends the existing `style_suffix` + immutable `STYLE_ANCHOR` for cross-image brand cohesion — reusing the exact `_compose_with_anchor` machinery the character/synopsis/result builders use. `qa_image_alt` produces a concise decorative alt.
- **`QaImageGenerator`** (`backend/app/services/icons/qa_pipeline.py`): build-time, per Q&A string — derives the deterministic seed (`derive_seed`), **dedups** via `media_assets.prompt_hash` (`find_media_asset_by_prompt_hash` → reuse, $0), else generates through `FalLedger.guarded_generate`, and binds `image_url` / `image_alt` **additively** onto the question/option. Fail-open and idempotent (never overwrites). Stored via the existing FAL pass-through CDN URL (default `image_storage.provider="fal"`).
- **Wired into the existing build hook** (`backend/app/services/icons/hook.py::maybe_bind_icons`) behind a **sub-flag** `quizzical.images.qa_generated_images_enabled`, strictly downstream of `qa_icons_enabled`. Generation runs first (preferred); the $0 generic-icon binder then fills the rest (fallback). PRECOMPUTED at build time → **zero added quiz-request latency**.

### 3. Frontend rendering (flag-gated)
- New FE feature flag `features.qaImages`, surfaced by the backend `/config` endpoint from `settings.images` (env override `ENABLE_QA_IMAGES`), defaulting OFF.
- **`AnswerTile`** now resolves the bound image only when `qaImages` is on (`safeImageUrl` defence-in-depth, lazy, skeleton→fade-in, Logo fallback — all already present). Off ⇒ today's text+Logo tile.
- **`QuestionImage`** (new) + a flag-gated slot in **`QuestionView`** above the heading: tiny (128px), `loading="lazy"`, `decoding="async"`, fixed-size reserved slot (zero CLS), **fails open to nothing** on error (no cross-origin placeholder). Decorative; the question text remains the meaning carrier.

---

## Cost model + projected starter-pack spend vs $150

Measured from real starter packs: **25 Q&A strings per topic** (5 questions × 4 options + 5 stems). At **$0.011/image** (full coverage = one image per string):

| Topics | Images | Projected $ | % of $150 cap | Within cap? |
|------:|-------:|------------:|--------------:|:-----------:|
| 5 | 125 | $1.38 | 0.9% | ✅ |
| 25 | 625 | $6.88 | 4.6% | ✅ |
| 100 | 2,500 | $27.50 | 18.3% | ✅ |
| **250** | 6,250 | **$68.75** | 45.8% | ✅ |
| 500 | 12,500 | $137.50 | 91.7% | ✅ |
| 904 (all seeded slugs) | 22,600 | $248.60 | 165.7% | ❌ — **ledger blocks at $150** |

**Read:** the realistic starter set (~250 topics) lands at **~$69, comfortably under $150.** The full 904-slug catalogue at *full* coverage would exceed the cap — and that is exactly the scenario the ledger is built to stop: it blocks new FAL calls at $150 and the remaining strings degrade to generic icons. Real spend is materially **below** these ceilings because (a) the same-universe path is best reserved for concrete, universe-anchored strings (abstract/personality strings fall back to icons/no-image), and (b) `prompt_hash` dedup suppresses repeats across packs.

`max_topics_fully_covered_under_cap ≈ 545` at 25 strings/topic.

---

## Load-time / relevance / style findings

- **Load time (added):** ~0 on the quiz critical path. Binding is **precomputed at build time**; the FE renders one extra tiny, lazy, content-addressed `<img>` served with the existing `public, max-age=31536000, immutable` cache. Fixed-size slots ⇒ no layout shift. (The prototype measured CLS 0.003 for the icon variant; the generated-image slot uses the same reserved-size approach.)
- **Relevance / same-universe:** prompts anchor the universe **first** and pass it verbatim, so FAL grounds the scene in that world (FAL handles licensing on its side, exactly like the existing branded-character path). Sample prompts for Disney Princess / Hogwarts House / Star Wars / Greek God / Pokémon Type are in `qa_same_universe_samples.json` (25 samples).
- **Brand-style consistency:** every prompt ends with the configurable `style_suffix` + the immutable `STYLE_ANCHOR` ("unified illustrated quiz art style, single consistent palette…") and a deterministic per-(topic,string) seed, so a topic's images stay visually cohesive and re-renders are stable.
- **Cost:** **$0 FAL spent this round** (no local key). Model + ledger validated with a fake client; cap enforcement proven.

---

## What remains before flipping the flag

1. **One real FAL validation pass** (a handful of images, well under the ~$5 round budget) once a FAL key is available, to eyeball same-universe relevance + brand style on real FLUX output.
2. **Per-topic coverage policy / relevance gate.** Today the generator attempts every Q&A string. Decide which strings deserve generation (concrete/universe-anchored) vs fall back to a generic icon — reuse the embedder + a relevance threshold (the prototype's router) so abstract/personality strings don't burn budget on weak images.
3. **Optional evaluator gate** on generated Q&A images (reuse `media_assets.evaluator_score` + the existing image-evaluator) before binding, mirroring the character path's quality bar.
4. **Wire the precompute orchestrator** to seed `media_assets` rows for generated Q&A images (currently we bind the FAL CDN URL pass-through; dedup already reads `media_assets`, so persisting them closes the reuse loop across builds) and surface ledger spend in the operator cost UI alongside `v_topic_cost_30d`.
5. **Live (non-precomputed) path** (optional): run the same enrichment in the existing fail-open background task for live topics if desired.

## Files

- Backend: `backend/app/services/icons/fal_ledger.py`, `qa_pipeline.py`, `hook.py`; `backend/app/agent/tools/image_tools.py`; `backend/app/models/db.py`; `backend/db/init/init.sql`; `backend/app/core/config.py`; `backend/app/api/endpoints/config.py`.
- Frontend: `frontend/src/components/quiz/QuestionImage.tsx`, `QuestionView.tsx`, `AnswerTile.tsx`; `frontend/src/context/ConfigContext.tsx`; `frontend/src/types/config.ts`.
- Tests: `backend/tests/unit/services/icons/test_fal_ledger.py`, `test_qa_pipeline.py`, `test_hook_qa_generate.py`; `frontend/src/components/quiz/QaImageFlagGate.spec.tsx`.
- Eval artifacts: `specifications/prototype/qa_same_universe_eval.py`, `qa_same_universe_samples.json`, `qa_same_universe_cost_model.json`.
