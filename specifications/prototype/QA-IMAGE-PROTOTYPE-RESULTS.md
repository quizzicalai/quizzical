# Q&A Image-Enrichment — Working Prototype + Honest 5-Criteria Evaluation

**Branch:** `prototype/qa-image-enrichment`
**Date:** 2026-06-29
**Author:** parallel prototyping dev (isolated worktree)
**Status:** Exploratory v1 — a real, runnable, measured prototype. NOT production-ready. Several open questions remain (see §7).

This prototype builds and **measures** brand-colored clipart on quiz questions & answers,
against the 5 criteria in the task and honoring the *Path-to-GO* from the plan's Adversarial
Skeptical Review. Where the original plan over-claimed (FAL cost-guard, "leverages existing
infra", `Image.tsx` fallback), this prototype **does the work and reports the real numbers**.

## What was built (all runnable)

| Artifact | Path | What it does |
|---|---|---|
| Routing engine | `routing/router.py` | 384-dim semantic NN Q&A→icon router; `eval` / `scale` / `bind`; local + OpenAI backends |
| FP analysis | `routing/analyze.py` | lists wrong-icon / abstract-string false positives at a chosen τ |
| Icon catalog | `data/icon_catalog.json` | 119 icons, each w/ a `name_caption` (Iconify-alias-like) **and** a `rich_caption` (enriched) for the caption-quality ablation |
| Labeled eval set | `data/qa_labeled.json` | 126 stratified, hand-labeled, realistic agent-style Q&A strings incl. adversarial near-misses |
| Recolor pipeline | `icons/recolor.mjs` | pulls **real Lucide (ISC)** SVGs, brand two-tone recolor → `icons/recolored/*.svg` + `brand-grid.html` + `THIRD-PARTY-ICONS.md` |
| FE sprite/bindings | `icons/build-sprite.mjs`, `icons/build-bindings.mjs` | emit inline-SVG sprite + precomputed text→iconId map into `frontend/src/proto/` |
| FE component | `frontend/src/proto/QaIcon.tsx` | fixed-size, reserved-space, inline, decorative, fail-open icon badge behind `VITE_PROTO_QA_ICONS` |
| FE integration | `QuestionView.tsx`, `AnswerGrid.tsx` | renders routed icons in the real quiz components, behind the flag |
| Demo + screenshots | `frontend/src/proto/QaIconsDemoPage.tsx` (`/dev/qa-icons`), `screenshots/` | real components rendered + Playwright-captured |

### Reproduce
```bash
# routing eval (local model)
.venv-proto/Scripts/python routing/router.py eval  --backend local  --captions rich
.venv-proto/Scripts/python routing/router.py eval  --backend local  --captions name   # ablation
.venv-proto/Scripts/python routing/router.py scale --backend local                     # 1k..100k timing
# OpenAI backend (key pulled from Key Vault into env, never logged):
OPENAI_API_KEY=$(az keyvault secret show --vault-name quizzical-shared-kv --name openai-api-key --query value -o tsv) \
  .venv-proto/Scripts/python routing/router.py eval --backend openai --captions rich
# icons + FE assets
cd icons && node recolor.mjs && node build-sprite.mjs && node build-bindings.mjs
# screenshots (needs Vite up: cd frontend && VITE_PROTO_QA_ICONS=1 npx vite --port 5180)
cd frontend && node proto-screenshot.mjs
```

---

## Criterion 1 — Low load time ✅ (demonstrated ~0 added critical-path cost)

**Design (matches plan §6, fixes the skeptic's findings):**
- **Routing is precomputed** — `router.py bind` resolves `{text → iconId}` once; the FE does **zero** embedding/NN/network at render time. In production this binding rides inside the pack/`session_history` payload exactly like the resolved character `image_url` does today.
- **Inline SVG sprite, not `<img>`** — `QaIcon` injects the recolored SVG from a build-time sprite. This is *stronger* than the plan's `<img>` path: **zero extra HTTP requests**, bytes ship with the already-cached JS. It also sidesteps the skeptic's Risk #5 (the repo's `Image.tsx` `onError` hits a cross-origin `placehold.co`) — `QaIcon` fails open to **nothing**.
- **Reserved, fixed-size slots** — the question icon sits in a fixed `h-12` row above the heading; answer icons are a fixed `0 0 32px` inline badge. Adding/removing an icon cannot shift layout.
- **Decorative** — `aria-hidden`, no alt text. Meaningful content stays in the Q/A text (fixes the a11y gap the skeptic flagged).

**Measured** (`data/loadtime_measurement.json`, Playwright + PerformanceObserver, on the real `QuestionView`/`AnswerGrid` at `/dev/qa-icons`):

| Metric | Result | Bar (plan R3) |
|---|---|---|
| CLS across 3 question swaps (icon slots changing) | **0.00311** | ≤ 0.01 ✅ |
| Icon-attributable image HTTP requests | **0** (the 1 image req is the app's own header logo) | minimal ✅ |
| Per-icon transfer | **0 KB over the wire** (inline; ~0.5–1 KB of already-shipped JS each) | ≤ 4 KB ✅ |

**Honest caveat:** "0 added latency" holds for the **inline-sprite** delivery used here, which is ideal for a fixed library of a few hundred Q/A icons. For a **tens-of-thousands** library (criterion 2) you cannot inline all of it — you'd serve the *selected* icons as content-addressed CDN `<img>` (still lazy/cached/immutable, still URL-in-payload), which adds a one-time ≤2–4 KB cached fetch per distinct icon. That is still off the critical path, but it is not literally "0 bytes" like the inline demo. Both paths are valid; the demo proves the strongest one.

---

## Criterion 2 — Scalable to tens of thousands of icons ✅ (approach + timing)

**Approach:** routing is **vector nearest-neighbour over an icon embedding index**, identical in shape to the repo's `precompute/lookup.py::_vector_nn` (`ORDER BY embedding <=> :q` on Postgres+pgvector with an IVFFlat cosine index; in-Python cosine under SQLite). Adding an icon = one row + one 384-dim embedding; no code change. This is the same substrate `topics.embedding`/`session_history.synopsis_embedding` already use.

**Measured build-time NN cost** (`data/scale_local.json`) — brute-force matmul (the *conservative upper bound*; pgvector IVFFlat is sub-linear and faster). Real 126-query batch, min-of-20 reps, single BLAS thread:

| Icon index size | per-query (µs) | per-query (ms) |
|---|---|---|
| 1,000 | 6.6 | 0.007 |
| 5,000 | 40.7 | 0.041 |
| 10,000 | 79.4 | 0.079 |
| 50,000 | 411 | 0.41 |
| 100,000 | 822 | 0.82 |

Cost is linear in N (brute force) and **stays sub-millisecond per query even at 100k icons**. A 6-question pack with 4 options each (~30 strings) routes against a 50k library in **~12 ms** at build time. Because this runs **at pack-build time, runtime cost is exactly zero** regardless of library size. On Postgres IVFFlat the build-time number is lower still.

**Honest caveat:** this measures the *NN search*, not *embedding* the captions. Embedding 10k–50k captions is a one-time offline job (local model ≈ a few hundred/sec on CPU). The skeptic's "bound build-time at scale" ask is answered for the search step; the embed step is one-time and cached via `embeddings_cache`.

---

## Criterion 3 — Reflective of non-deterministic Q&A (the make-or-break) — measured, candid

This is the criterion the skeptic flagged as the central risk. I ran a **stratified, hand-labeled, non-cherry-picked** sample of **126** realistic agent-style strings (questions + answer options across ~25 topics: personality quizzes, science, careers, food, travel, fantasy, etc.), including **18 deliberately abstract** strings where the correct answer is **no icon** ("Pick a motto", "What motivates you most?") and **adversarial near-misses** ("Mercury" the planet/element/car, "Au", "Gryffindor"). Acceptable icons were labeled per item; precision is scored on the **top-1 shown** icon.

I tested **two embedding approaches** (criterion's ≥2 requirement) and **ablated caption quality**.

### Headline results — operating point = max coverage subject to FP ≤ 5% AND precision@1 ≥ 80%

| Approach | τ | **precision@1** | **FP rate** | **coverage** | **no-icon rate** |
|---|---|---|---|---|---|
| **Local bge-small-en-v1.5 (384d) + rich captions** | 0.66 | **0.918** | **0.048** | **0.620** | 0.421 |
| Local bge-small + name-only captions (ablation) | 0.70 | 0.886 | 0.040 | 0.361 | 0.651 |
| OpenAI text-embedding-3-small @384 + rich captions | 0.44 | 0.902 | 0.048 | 0.509 | 0.516 |

**Definitions:** precision@1 = of icons actually *shown*, fraction on-topic. FP rate = fraction of *all* items that show a wrong icon **or** show any icon on an abstract "no-icon" string. coverage = of icon-*eligible* items, fraction that got a correct icon. no-icon rate = fraction shown nothing.

### Findings (honest)

1. **The plan's R2 bar is achievable** — local model + rich captions clears precision@1 ≥ 80% and FP ≤ 5% **simultaneously** at τ=0.66, with 62% coverage. The skeptic's worry that "the FP≤5% ∧ precision≥80% ∧ coverage≥50% triple may be jointly unsatisfiable" is **disproven for this sample** (all three hold).
2. **Caption quality IS the dominant lever** (skeptic Risk #3 confirmed): swapping rich captions for bare name-only captions, at the same FP bar, **drops coverage from 62% → 36%**. Investing in good captions matters far more than the embedding model.
3. **Local 384-dim beats hosted, and is free.** bge-small (matching the repo's `Vector(384)`) gives higher coverage at the FP bar (62%) than OpenAI@384 (51%), at $0 and no network hop. The two models live on **different similarity scales** (local NN sims peak ~0.85; OpenAI@384 peaks ~0.70), so **τ must be tuned per model** — you cannot reuse the topic-NN `0.86` anchor.
4. **The no-icon fallback degrades safely.** At τ=0.66, **42% of strings correctly get no icon** — including all the adversarial traps ("Mercury", "Au", "Gryffindor" → below threshold → nothing). A wrong icon never beats no icon, and the threshold enforces that.
5. **The few false positives are mostly defensible near-misses** (`analyze.py` at τ=0.66): "Exploring a foreign city" → map (I labeled building/world), "Helping others" → people-group. Only one true abstract-trap fired ("Fortune favors the bold" → money). My labeling was strict, so *user-perceived* precision is likely a bit higher than 91.8%.

### Candid limitations on routing
- **Sample size (126) is still smallish.** The skeptic wanted ≥1,000, multi-annotator with inter-rater agreement. This is a one-author sample; treat the numbers as a strong *directional* signal, not a final QA gate.
- **Coverage @ FP≤5% is ~60%, not ~85%.** Many abstract personality strings *should* get no icon — that suppresses raw coverage but is *correct*. Still, "every Q/A gets a delightful icon" is **not** what this delivers; "~3 of 5 concrete Q/A get a good icon, abstract ones get none" is the honest product shape.
- **Asymmetric retrieval is real** (skeptic A.3): the symmetric mini-model handles short concrete nouns well but is weaker on indirect/abstract phrasings. Rich captions mitigate but don't eliminate this.
- **Caption authoring is itself an un-budgeted LLM job** at production scale (6–10k captions). The ablation proves it's worth doing; it is not free.

---

## Criterion 4 — Consistent, fun, brand-aligned style ✅ (recolored real open set, grid produced)

- **Source:** real **Lucide** icons via the `lucide-static` npm package — **ISC license** (permissive MIT-equivalent; recolor + redistribute allowed). 1,993 icons available; 119 used. *(Correction to the plan: lucide-static is ISC, not MIT — recorded accurately in `THIRD-PARTY-ICONS.md`.)*
- **Recolor:** `recolor.mjs` forces the brand stroke, adds a soft two-tone rounded wash backing, keeps Lucide's native 24×24 / 2px / round caps — which already match `Logo.tsx` exactly, so the set is cohesive by construction.
- **Palette (plan §2):** sea-blue `#0079AE` primary (68 icons), indigo `#4F46E5` "smart/abstract" (14), amber `#D97706` "fun/energy" (18), slate neutral (19). Variant is assigned by **semantic role** (science→indigo, fun/food→amber, tools/UI→slate) so color carries meaning, not noise.
- **License hygiene:** `THIRD-PARTY-ICONS.md` auto-generated with the ISC notice retained + per-icon source/variant table (addresses the skeptic's "license-retention as an acceptance gate").
- **Evidence:** `screenshots/brand-icon-grid.png` — all 119 recolored icons in one cohesive two-tone system; `screenshots/quiz-icons-q*.png` — icons in the live quiz tiles.

**Candid limitations on style**
- **Two-tone via baked SVG = no live dark-mode theming** (skeptic A.2). The recolored SVGs carry fixed hexes; on a dark surface the wash backing would need a dark variant. Fix is a 2× variant set or CSS-var injection for the inline path (the inline path *can* be themed; the eventual `<img>` path cannot). Not solved here.
- **Wash backing on every icon** reads as a "chip". That's a deliberate style choice; an alternative is line-art only (monotone) which is lighter. Worth an A/B with design.
- **§5 character-art-as-Q/A-icon was NOT built** — the skeptic correctly argued full-color character art clashes with two-tone line icons. Demoting it to hero surfaces only; out of scope for this icon prototype.

---

## Criterion 5 — Cost efficient ✅ (library-first = $0; FAL only for gaps)

- **The 119-icon demo library cost $0** (recolored open SVGs). The approach scales to the full library at **$0 of FAL** — open sets (Lucide ISC + Tabler/Phosphor MIT + Material/MDI Apache-2.0) cover the common long tail.
- **Embedding cost ≈ $0**: local 384-dim model is free. Even hosted, embedding ~10k captions (~50k tokens) ≈ **$0.001** at `text-embedding-3-small` rates.
- **FAL reserved for gaps only.** Honest gap-fill estimate, correcting the plan's optimism per the skeptic:
  - FLUX schnell **$0.003 / image** (≤1 MP, billed rounded up) — *unit price HOLDS*.
  - FLUX dev is **$0.025 / megapixel** (NOT flat per image — skeptic A.1 correction); coincides with $0.025 only for ≤1 MP icons.
  - Realistic gap-fill: if the open library covers ~85% of *eligible* concepts (to be confirmed by an R4-style coverage run — not done at scale here), the remaining ~15% specialized icons at, say, 2–4k kept images and 3–5 attempts each (retry/fallback multiplication, skeptic A.1) ≈ **$30–$60 of schnell**. Comfortably inside a $150 cap.
- **This prototype spent $0 of FAL** (used the free library, as the brief preferred).

**Candid limitation / honored Path-to-GO item:** the plan's claim that the existing `cost_guard.py` enforces a FAL ceiling is **false** — verified again here: `cost_guard.py`/`cost.py` sum `precompute_jobs.cost_cents` (LLM *text* spend); **FAL image spend is recorded nowhere** and `cost_cents` is a `SMALLINT`. **A real FAL $-ledger + lifetime accumulator must be built before any FAL spend.** This prototype avoids the issue entirely by spending $0, but the gap is real and is the #1 prerequisite for the gap-fill phase.

---

## §7 — Top open questions / next steps (candid)

1. **Scale the routing eval.** 126 hand-labeled items → ≥1,000, multi-annotator, with inter-rater agreement and stratification by string length + abstract-vs-concrete. The 91.8%/4.8% numbers are a strong signal, not a ship gate.
2. **Build the FAL $-ledger** (the plan's most dangerous wrong claim) before spending a cent on gap-fill. Lifetime accumulator + per-round caps + the FAL client writing to it.
3. **Prove library coverage at scale (R4).** Run the router over thousands of real Q/A against the *full* recolored library at the locked τ to get the true distinct-concept coverage and the real gap list — replacing the plan's inflated "20k icons / 85%" headline with measured numbers.
4. **Caption generation pipeline.** The ablation proves rich captions are worth ~+26 pts of coverage. Decide how to author 6–10k of them (LLM + Iconify aliases), budget it, and QA it.
5. **Dark-mode two-tone story.** Decide baked-2×-variants vs inline-CSS-var theming for the `<img>` delivery path.
6. **Build-time binding wiring.** Land the 384-dim `embed_fn` (the repo's `embed_fn=None` blocker) and add icon binding into `builder.py` + the live fail-open background path — none of which exists yet (skeptic A.4). This prototype stands in with a static binding file.
7. **Per-surface alt-text policy.** Decorative here (`aria-hidden`), but confirm with a11y review; some surfaces may want meaningful alt.
8. **Style A/B:** wash-chip vs monotone line-art; question-icon-on vs answers-only.

---

## Bottom line

A real, runnable prototype that **routes arbitrary agent Q&A → brand icon via 384-dim semantic NN with a relevance threshold + no-icon fallback**, renders it in the actual quiz components with **measured ~0 added load cost (CLS 0.003, 0 icon requests)**, scales to **100k icons at <1 ms/query build-time**, on a **$0 recolored-Lucide library** in a cohesive brand two-tone system. Routing relevance on a varied, honestly-labeled sample: **precision@1 0.918, FP 4.8%, coverage 62%, no-icon 42%** (local model + rich captions). The make-or-break criterion is **promising but not yet proven at production scale** — caption quality is the dominant lever, coverage at the FP bar is ~60% by design (abstract strings correctly get nothing), and the FAL cost-ledger remains unbuilt. v1 is a GO-able foundation, not a finished feature.
