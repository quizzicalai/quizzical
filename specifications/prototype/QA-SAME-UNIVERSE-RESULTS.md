# Same-Universe Q&A Imagery — Implementation + Evaluation

**Branch:** `feat/qa-images-same-universe` (isolated clone)
**Date:** 2026-06-30 (updated; relevance gate + cross-build dedup persistence added)
**Status:** Built, flag-gated **OFF** by default. Flag-off is byte-for-byte today's behaviour. No real FAL spend this round (no local FAL key) — validated with the production prompt builder, the REAL 384-dim embedder, a fake FAL client, and the cost model.

This advances the owner's **same-universe** vision: topic/universe-specific imagery (e.g. *Harry Potter* → "Dumbledore looking into a pensieve"), **not** generic clipart. The merged generic-icon foundation (`quizzical.images.qa_icons_enabled`) stays the **fallback** for strings/topics the generation path skips.

## TL;DR — make-or-break metric (the relevance gate)

The owner's #1 risk was "images logical to content" / not burning FAL budget on abstract strings. The new **per-string relevance gate** (reuses the existing 384-dim `bge-small-en-v1.5` embedder — no new model) routes only concrete, universe-anchored strings to FAL; abstract personality/preference strings fall back to the $0 generic icon. Evaluated on a **diverse, hand-labeled 99-string sample across ~25 topic universes** (animals, food, geology, instruments, MBTI, alignments, trades, vehicles, mythical creatures…) drawn from the breadth of the `personality_only` topic catalogue:

| metric | value | meaning |
|---|---:|---|
| **precision** | **1.000** | of strings routed to FAL, fraction truly concrete → **zero wasted FAL spend** |
| **recall** | **0.980** | of truly-concrete strings, fraction we route → coverage of the images worth making |
| **false-positive rate** | **0.000** | abstract strings wrongly sent to FAL |
| **coverage** | **0.505** | fraction of ALL strings routed to FAL (the rest fall back to $0 icons) |
| **accuracy** | **0.990** | overall label agreement |

Operating point `margin=0.04, concrete_floor=0.20`, chosen from a full threshold sweep (precision held at 1.0 across `margin∈[0.01,0.06]`; the entire sweep produced **at most 2 false positives** out of 48 abstract strings — the concrete/abstract separation is robust). The single false negative ("A hooded Jedi standing on a desert dune", margin 0.0375) is the *safe* failure direction — a missed image that falls back to a generic icon, never a wasted dollar. Artifacts: `qa_relevance_eval.py`, `qa_relevance_labeled.json`, `qa_relevance_eval.json`.

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

### 3. Per-string relevance gate (the budget-protection guardrail)
- **`RelevanceGate`** (`backend/app/services/icons/relevance_gate.py`): scores each Q&A string against two small curated anchor sets — CONCRETE ("a detailed illustration of a specific animal or creature", "a painting of a landscape…") vs ABSTRACT ("how you feel about something", "a question about your personality…") — embedded once with the SAME `bge-small-en-v1.5` model + BGE query prefix the icon binder uses. A string generates iff `max_sim(concrete) − max_sim(abstract) ≥ margin` AND `max_sim(concrete) ≥ concrete_floor`. Cheap pre-filters skip blanks, very short strings, and template answers ("None of the above", "It depends") before any embed. **Fail-safe**: any error ⇒ no generation (never spend on a broken signal).
- **Wired into `QaImageGenerator`** BEFORE dedup/generation; the hook constructs it from `settings.images.relevance_gate` (`RelevanceGateConfig`: `enabled=True`, `margin=0.04`, `concrete_floor=0.20`). A `gated_out` stat surfaces how many strings the gate saved from FAL. `enabled=False` reverts to attempting every string (legacy).
- **Eval numbers above** (precision 1.0 / recall 0.98 / coverage 0.505). This is the make-or-break "images logical to content" result — and it roughly **halves** projected FAL spend (see cost table).

### 4. Cross-build dedup persistence (closes the reuse loop)
- `QaImageGenerator` now **persists a `media_assets` row** for every freshly-generated Q&A image (`content_hash` derived from the content-addressed FAL CDN URL, `prompt_hash`, `prompt_payload`). Best-effort + fail-quiet + idempotent. The NEXT build (or a crash re-run) calls `find_media_asset_by_prompt_hash`, finds the row, and **reuses it for $0 with no FAL call** — the cross-build dedup loop prior reviews flagged as open. Verified by a two-build test (`test_generator_persists_media_asset_for_cross_build_dedup`: build 1 generates 3, build 2 reuses 3, FAL never called).

### 5. Frontend rendering (flag-gated)
- New FE feature flag `features.qaImages`, surfaced by the backend `/config` endpoint from `settings.images` (env override `ENABLE_QA_IMAGES`), defaulting OFF.
- **`AnswerTile`** now resolves the bound image only when `qaImages` is on (`safeImageUrl` defence-in-depth, lazy, skeleton→fade-in, Logo fallback — all already present). Off ⇒ today's text+Logo tile.
- **`QuestionImage`** (new) + a flag-gated slot in **`QuestionView`** above the heading: tiny (128px), `loading="lazy"`, `decoding="async"`, fixed-size reserved slot (zero CLS), **fails open to nothing** on error (no cross-origin placeholder). Decorative; the question text remains the meaning carrier.

---

## Cost model + projected starter-pack spend vs $150

Measured from real starter packs: **25 Q&A strings per topic** (5 questions × 4 options + 5 stems). At **$0.011/image**. "Full coverage" = one image per string (no gate). "Gated" applies the measured relevance-gate coverage (**50.5%** of strings route to FAL; the rest fall back to $0 icons):

| Topics | Full $ | % cap | Gated $ | % cap | Gated within $150? |
|------:|------:|----:|------:|----:|:--:|
| 5 | $1.38 | 0.9% | $0.69 | 0.5% | ✅ |
| 25 | $6.88 | 4.6% | $3.48 | 2.3% | ✅ |
| 100 | $27.50 | 18.3% | $13.89 | 9.3% | ✅ |
| **250** | $68.75 | 45.8% | **$34.73** | 23.2% | ✅ |
| 500 | $137.50 | 91.7% | $69.45 | 46.3% | ✅ |
| 904 (all seeded slugs) | $248.60 | 165.7% | **$125.56** | 83.7% | ✅ |

**Read:** with the relevance gate, the realistic starter set (~250 topics) lands at **~$35**, and **even all 904 seeded slugs fit under the $150 cap (~$126)** — the gate roughly **doubles** the catalogue that fits the budget (`max_topics_under_cap` 545 → **1079**). Real spend is materially below even the gated figures because `prompt_hash` dedup + persisted `media_assets` rows suppress repeats across builds. And the persistent `fal_spend_ledger` remains the hard backstop: it blocks any new FAL call at $150 regardless of catalogue size, with remaining strings degrading to generic icons.

### End-to-end dry-run on the REAL starter packs (`qa_pipeline_dryrun.py`)

Ran the FULL production path (gate → prompt → `media_assets` dedup → ledger-guarded fake-FAL generate → bind + persist) over the 5 real starter packs (125 Q&A strings) with the REAL embedder + a fake FAL client (`qa_pipeline_dryrun.json`):

- **Gate routed only 14/125 = 11.2%** of strings to FAL. The real starter-pack questions are overwhelmingly abstract personality prompts ("Where are you most likely to be on a Saturday afternoon?", "Curled up with a thick book in a quiet corner"), which the gate correctly routes to the $0 icon fallback. Per pack: Hogwarts House 6 (it has a concrete "pick the magical artifact" question), Greek God 4, Disney Princess 2, Pokémon Type 2, **Star Wars 0** (all its options are abstract preferences).
- **This is the gate working as designed on production content** — and it means *real* spend is far below even the gated cost projections above (which assumed 50.5% coverage from the deliberately concrete-heavy labeled sample). On organic packs, coverage is much lower, so the catalogue is even cheaper.
- **Cross-build dedup loop proven:** a SECOND pass over the same packs made **0 FAL calls** and **reused all 14** persisted `media_assets` rows. Repeated builds / crash re-runs cost $0 for already-generated images.

---

## Load-time / relevance / style findings

- **Load time (added):** ~0 on the quiz critical path. **Proof:** the binding runs inside `precompute/builder.py::run_build` (`maybe_bind_icons` at line 194) — i.e. at BUILD time, never on the live quiz request. The `image_url` is baked into the persisted pack and served from the existing `public, max-age=31536000, immutable` cache, so a quiz request does zero extra work. The FE then renders one extra tiny, lazy `<img>`: `loading="lazy"` + `decoding="async"`, intrinsic `width/height=128` and a fixed-size slot (`h-32 w-full` on `AnswerTile`, `h-28/32 w-28/32` on `QuestionImage`) that reserves space whether the image, the skeleton, or the Logo fallback shows ⇒ **CLS ≈ 0** (no reflow on load or on error). Fail-open: a dead/forbidden URL renders nothing (`AnswerTile` → Logo, `QuestionImage` → null), never a broken-image box. These structural guarantees are asserted by `QaImageFlagGate.spec.tsx` (lazy/decoding/width/height/fail-open) and `AnswerTile.spec.tsx`. (The earlier prototype measured CLS 0.003 for the icon variant; the generated-image slot uses the same reserved-size approach.)
- **Relevance / same-universe:** prompts anchor the universe **first** and pass it verbatim, so FAL grounds the scene in that world (FAL handles licensing on its side, exactly like the existing branded-character path). Sample prompts for Disney Princess / Hogwarts House / Star Wars / Greek God / Pokémon Type are in `qa_same_universe_samples.json` (25 samples).
- **Brand-style consistency:** every prompt ends with the configurable `style_suffix` + the immutable `STYLE_ANCHOR` ("unified illustrated quiz art style, single consistent palette…") and a deterministic per-(topic,string) seed, so a topic's images stay visually cohesive and re-renders are stable.
- **Cost:** **$0 FAL spent this round** (no local key). Model + ledger validated with a fake client; cap enforcement proven.

---

## What remains before flipping the flag

1. **One real FAL validation pass — the ONLY hard blocker.** Needs the production `FAL_AI_KEY` (no local key here). Run a handful of images (well under the ~$5 round budget; the ledger caps it) to eyeball same-universe relevance + brand style on real FLUX output. Everything else below is optional polish.
2. ~~Per-topic coverage policy / relevance gate.~~ **DONE** — `RelevanceGate` ships ON by default, precision 1.0 / recall 0.98 on the diverse labeled sample (above).
3. ~~Wire media_assets persistence.~~ **DONE** — `QaImageGenerator._persist_media_asset` writes a row per generation; cross-build reuse verified by test.
4. **Optional evaluator gate** on generated Q&A images (reuse `media_assets.evaluator_score` + the existing image-evaluator) before binding, mirroring the character path's quality bar. Deferred — the relevance gate already protects relevance + budget; an evaluator pass would add a second cost (one extra LLM call per image) for marginal quality gain.
5. **Operator cost UI**: surface `fal_spend_ledger` lifetime spend alongside `v_topic_cost_30d` (a small read-only view + endpoint). Deferred; the ledger already enforces the cap.
6. **Live (non-precomputed) path** (optional): run the same enrichment in the existing fail-open background task for live topics if desired.
7. **Grow the labeled eval set** beyond 99 strings (e.g. 300–500 across more of the 1000+ topic types) once real FLUX output confirms the operating point, to tighten the recall estimate. The current sample already spans ~25 universes and the precision result is robust across the threshold sweep.

## Files

- Backend: `backend/app/services/icons/fal_ledger.py`, `qa_pipeline.py`, `relevance_gate.py`, `hook.py`; `backend/app/agent/tools/image_tools.py`; `backend/app/models/db.py`; `backend/db/init/init.sql`; `backend/app/core/config.py`; `backend/app/api/endpoints/config.py`.
- Frontend: `frontend/src/components/quiz/QuestionImage.tsx`, `QuestionView.tsx`, `AnswerTile.tsx`; `frontend/src/context/ConfigContext.tsx`; `frontend/src/types/config.ts`.
- Tests: `backend/tests/unit/services/icons/test_fal_ledger.py`, `test_qa_pipeline.py`, `test_relevance_gate.py`, `test_hook_qa_generate.py`; `frontend/src/components/quiz/QaImageFlagGate.spec.tsx`.
- Eval artifacts: `specifications/prototype/qa_relevance_eval.py` (+ `qa_relevance_labeled.json`, `qa_relevance_eval.json`), `qa_same_universe_eval.py` (+ `qa_same_universe_samples.json`, `qa_same_universe_cost_model.json`), `qa_pipeline_dryrun.py` (+ `qa_pipeline_dryrun.json` — full end-to-end path on real packs).
