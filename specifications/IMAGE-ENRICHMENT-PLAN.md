# Image Enrichment Plan — Brand-Colored Question/Answer Icons

**Status:** Draft / authoring only (no code, no image generation, no git changes performed)
**Date:** 2026-06-29
**Owner:** eric@hyperproof.io
**Scope:** Add classy, minimal, **brand-colored** small icon/clipart images to quiz **questions** and **answers** (and a little visual interest on the **home** page) to make Quafel more fun — **with zero added load time** — plus a system to **reuse** pre-generated canonical-topic character art as Q/A imagery.

---

## 0. TL;DR Summary

- **Architecture: HYBRID, library-first.** Build a static, brand-recolored icon library of **~20,000** small SVG/WebP assets sourced overwhelmingly from existing **permissively-licensed** open icon sets (Material Symbols, MDI, Tabler, Phosphor, Lucide — all MIT/Apache-2.0), recolored programmatically to the Quafel brand palette. Reserve **FAL** image generation only for the small fraction of specialized/branded icons the open sets miss.
- **Routing: semantic nearest-neighbor.** At runtime, embed the question/answer text and pick the icon whose caption/tag embedding is the closest cosine match, **above a relevance threshold τ_icon**, else show **no icon** (graceful fallback). This reuses the repo's existing `Vector(384)` pgvector infra and `embeddings_cache`. **No per-quiz image generation** — every served asset is static and content-addressed.
- **Load time: genuinely zero added latency.** Icons are static, content-addressed assets on the existing Azure Blob/CDN path (or inlined SVG sprite for the tiny home-page set), `loading="lazy"`, tiny (target ≤ 2–4 KB each), `async`, behind the existing `public, max-age=31536000, immutable` cache header, and never on the quiz's critical render path. The routing decision is precomputed into the pack at build time, so **runtime adds zero model calls and zero blocking fetches**.
- **Budget: $150 on FAL, comfortably.** The bulk library is **$0 of FAL** (recolored open SVGs). FAL is used only for (a) style calibration experiments and (b) gap-filling specialized icons. Realistic spend: **~$70–$110 of the $150**, leaving a buffer. (FLUX schnell = **$0.003/megapixel, billed rounded up to the nearest MP** — a 512×512 icon = 1 MP = $0.003. [fal.ai](https://fal.ai/models/fal-ai/flux/schnell))
- **Reuse: canonical character art.** Pre-generate character descriptions + images for popular/canonical topics and reuse them across topics via the existing `canonical_key` dedup + `Vector(384)` embedding, so common subjects (e.g., a wizard, a cat, a rocket) load instantly anywhere they recur.

---

## 1. Grounding in the Real Architecture

All of the following were read directly in the repo; the plan is built on them, not on assumptions.

### 1.1 Image pipeline & FAL client
- `backend/app/services/image_pipeline.py` — background-task orchestration for FAL generation. Key properties the icon system inherits:
  - **Fail-open, never blocks the user response** (functions are scheduled via FastAPI `BackgroundTasks` after persistence returns).
  - **HEAD-probe reuse cache** (`_url_alive` + `_get_character_url`): if a DB `image_url` is already live, the FAL call is skipped — "what makes precomputed packs render instantly on a cold start."
  - **Null-retry + transient-retry** wrappers around `_client.generate`.
  - A **branded character fallback ladder** (`_generate_character_with_brand_fallback`) — literal → LLM physical description → stricter description.
- `backend/app/agent/tools/image_tools.py` — prompt builders (`build_character_image_prompt`, `build_synopsis_image_prompt`, `build_result_image_prompt`, `build_branded_attempt_prompt`, `build_descriptive_attempt_prompt`), a `STYLE_ANCHOR` constant, and `derive_seed(session_id, subject)` for deterministic, cohesive output.

### 1.2 Config knobs (`backend/app/core/config.py`)
- `ImageGenSettings`: `enabled=True`, `provider="fal"`, **`model="fal-ai/flux/schnell"`**, **`image_size={"width":512,"height":512}`**, `num_inference_steps=2`, `timeout_s=10.0`, `concurrency=6`, `style_suffix`, `negative_prompt`, `url_allowlist=["fal.media","v2.fal.media","v3.fal.media"]`, `retry=RetryConfig(max_attempts=2,...)`.
- `ImageStorageConfig`: `provider: Literal["fal","local"]="fal"`, `rehost_window_days=7`, **`cache_control="public, max-age=31536000, immutable"`**. (Azure `blob` provider exists in code; the literal will widen in Phase 12.)

### 1.3 Storage (content-addressed) — `backend/app/services/precompute/storage.py` + `MediaAsset`
- `MediaAsset` (`backend/app/models/db.py`, ~line 452): `content_hash` (unique, indexed), `prompt_hash` (indexed), `storage_provider` (`'fal' | 'local' | 'blob' | 'blob+cdn'`), `storage_uri`, `bytes_blob`, `prompt_payload` (JSONB), `evaluator_score` (1–10), `flag_count`, `expires_at`. **Deduped by `content_hash`.**
- Providers: `LocalProvider` (`/api/v1/media/{id}`), `FalProvider` (pass-through `storage_uri`), `AzureBlobProvider` (uploads keyed by `content_hash`, returns `base_url/container/content_hash`), and `DualWriteResolver` (prefers `blob`/`blob+cdn`, falls back to local). **This is the natural home for the icon library.**

### 1.4 Embedding / pgvector infra (the routing substrate)
- **Dimension is `Vector(384)` everywhere** — `Character.embedding`, `Topic.embedding`, `CharacterSet.synopsis_embedding`, and **`EmbeddingsCache.embedding`** (`backend/app/models/db.py`). 384 dims ⇒ a sentence-transformer-class model (e.g., `bge-small-en-v1.5` or `all-MiniLM-L6-v2`), **not** OpenAI's 1536-dim models. **Icon-caption embeddings MUST be 384-dim and produced by the same model** to share the cosine space.
- `backend/app/services/embeddings/cache.py` — `get_or_compute_embedding(session, text, *, model, dim, embed_fn)` dedups every embedding on a SHA-256 `text_hash` against `embeddings_cache(text_hash, model, dim, embedding)`. **Embed once, ever.** Reuse this directly for both Q/A text and icon captions.
- `backend/app/services/precompute/lookup.py` — `PrecomputeLookup._vector_nn` already implements the pgvector path: `ORDER BY embedding <=> :q` on Postgres, in-Python cosine under SQLite, with a `LookupThresholds.match = 0.86` cutoff. **This is the exact pattern to mirror for icon routing.**
- **pgvector indexes already exist** (`backend/db/init/init.sql`): `CREATE EXTENSION vector` plus **IVFFlat cosine indexes** (`USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)`) on `topics.embedding` and `session_history.synopsis_embedding`. The icon table must add the **same** index type on its embedding column for ANN at scale.
- `backend/app/services/precompute/evaluator.py` — character reuse quality gate: **`CROSS_PACK_MIN_COSINE = 0.85`** (`is_cross_pack_consistent`) rejects a divergent new character profile and reuses the canonical one. Reuse this exact gate when deciding to reuse canonical character art across topics (§5).
- **GAP (critical):** `backend/app/api/dependencies.py` (~line 321) wires `embed_fn=None` — *"The embed_fn is None until Phase 7 wires the embeddings cache in, so vector NN is currently inert."* **No concrete 384-dim embedder is connected yet.** Icon routing depends on landing that embedder (see §6, R0). The wiring TODO is concrete: wrap `get_or_compute_embedding` in a closure binding `model`/`dim=384` and inject it as `embed_fn`.

### 1.5 Dedup / canonical_key (the reuse substrate)
- `backend/app/services/precompute/canonicalize.py` — `canonical_key_for_name` (NFKD accent-fold, whitespace-collapse, lowercase), pure-Python, deterministic.
- `backend/app/services/precompute/dedup.py` — `find_character_by_canonical_key` (`AC-PRECOMP-DEDUP-1`: reuse the existing `Character.id`, skip insert + embedding + image work) and `find_media_asset_by_prompt_hash` (`AC-PRECOMP-DEDUP-3`: reuse a prior FAL asset when `(prompt,provider,model)` collides and it cleared `min_evaluator_score`).
- `Character` table: `name` (unique), `short_description`, `profile_text`, `image_url`, **`canonical_key`** (indexed), **`embedding Vector(384)`**, `evaluator_score`.

### 1.6 Frontend (React + Vite + Tailwind on Azure Static Web Apps)
- Quiz/answer/result images already render `<img loading="lazy">`:
  - `frontend/src/components/quiz/AnswerTile.tsx` (skeleton + fade-in + `object-cover`), `frontend/src/components/quiz/SynopsisView.tsx`, `frontend/src/components/result/ResultProfile.tsx`.
  - `frontend/src/components/common/Image.tsx` — generic `<img>` wrapper with `onError` fallback.
- **Icons today are hand-coded inline TSX SVG** under `frontend/src/assets/icons/` (`Logo.tsx`, `CheckIcon.tsx`, mascot `WizardCatIcon.tsx`, social icons), all using `stroke="currentColor"` + Tailwind sizing (`w-8 h-8`). The "q" logo establishes the visual language.
- `frontend/src/lib/safeImageUrl.ts` allowlist already includes `blob.core.windows.net`, `azureedge.net`, `azurefd.net`, `azurestaticapps.net` — **Azure Blob/CDN icon URLs are already trusted by the FE.**
- `frontend/index.html` — `preconnect` to Google Fonts only (no image preconnect yet). CSP `img-src 'self' data: https:`. Vite chunk-splits vendor bundles.
- `frontend/staticwebapp.config.json` — SWA routing + global security headers; relies on backend `cache_control` for asset caching.

---

## 2. Brand Palette (derived from the primaries)

Primaries given: sea-blue `#0079AE`, indigo `#4F46E5`, amber `#D97706`, slate neutrals. Below is a full **named** palette with tints/shades so two-tone icons read consistently across light/dark surfaces. (Hexes for shades are designed values; the exact ramp can be locked in R1 against the live Tailwind theme tokens — `text-primary`, `text-secondary` already exist in the FE.)

### 2.1 Core ramps

| Token | Hex | Role |
|---|---|---|
| `brand.sea.700` | `#005E86` | sea-blue, deep (dark-surface line) |
| **`brand.sea.500`** | **`#0079AE`** | **PRIMARY sea-blue** (default icon stroke) |
| `brand.sea.300` | `#5BB6D6` | sea-blue, light (two-tone fill) |
| `brand.sea.100` | `#D6EEF6` | sea-blue wash (background chip) |
| `brand.indigo.700` | `#3730A3` | indigo, deep |
| **`brand.indigo.500`** | **`#4F46E5`** | **SECONDARY indigo** (accent stroke / "smart" topics) |
| `brand.indigo.300` | `#A5B4FC` | indigo, light (two-tone fill) |
| `brand.indigo.100` | `#E0E7FF` | indigo wash |
| `brand.amber.700` | `#B45309` | amber, deep |
| **`brand.amber.500`** | **`#D97706`** | **ACCENT amber** (energy/fun/highlight) |
| `brand.amber.300` | `#FCD34D` | amber, light (two-tone fill) |
| `brand.amber.100` | `#FEF3C7` | amber wash |

### 2.2 Slate neutrals (line/ground/disabled)

| Token | Hex | Role |
|---|---|---|
| `slate.900` | `#0F172A` | near-black ink (rare; high-contrast line) |
| `slate.700` | `#334155` | neutral icon stroke on light bg |
| `slate.400` | `#94A3B8` | muted / "no strong match" icon |
| `slate.200` | `#E2E8F0` | two-tone neutral fill |
| `slate.50` | `#F8FAFC` | transparent-equivalent ground |

### 2.3 Semantic assignments (opinionated)

- **Default question icon stroke** → `brand.sea.500`. **Default two-tone fill** → `brand.sea.100`.
- **Answer-option icons** → neutral `slate.700` stroke + `slate.200` fill so they don't compete with the selected/correct states already styled in `AnswerTile.tsx`.
- **"Smart/abstract" topics** (science, logic, history) → `brand.indigo.500` stroke + `brand.indigo.100` fill.
- **"Fun/energy" topics & home-page flourishes** → `brand.amber.500` stroke + `brand.amber.300` fill.
- **Correct / positive** → keep the existing FE success color; icon recolor must not collide with it.
- **No-strong-match fallback** (if an icon is shown at all) → `slate.400` monotone, low emphasis. (Default is *no icon* — see §4.4.)

---

## 3. Opinionated Clipart Style Spec

Goal: a single, recognizable "Quafel icon" language consistent with the clean "q" logo — **minimal flat / line-art, two-tone, transparent background.**

| Property | Spec |
|---|---|
| **Form** | Geometric line-art with optional single flat-fill backing shape (two-tone). No gradients, no shadows, no 3D, no skeuomorphism. |
| **Stroke weight** | **2.0 px on a 24×24 viewBox** (i.e., `stroke-width:2`, matching `Logo.tsx`). Scales proportionally for larger source rasters. |
| **Stroke caps/joins** | `stroke-linecap="round"`, `stroke-linejoin="round"` (friendly, matches logo). |
| **Corner radius** | Consistent rounding; rectangles/containers use ~`rx=3` on the 24-grid (~12.5% of side). No sharp 90° corners on container shapes. |
| **Fill rule** | Two-tone: outline in the **stroke token**, optional single backing fill in the matching **wash/light token** (`*.100`/`*.300`). `fill-rule:evenodd` for cutouts. Pure line-art (no fill) is the acceptable monotone variant. |
| **Palette assignment** | Per §2.3. **Exactly two brand colors per icon max** (stroke + fill). Never full-color illustration — that is reserved for character art (§5). |
| **Background** | **Transparent** (`fill="none"` ground). No baked plates. |
| **Grid/padding** | 24×24 viewBox, **2 px safe padding** (live area 20×20) so icons sit evenly next to text. |
| **Detail budget** | Readable at **16–20 px** rendered. ≤ ~12 path segments; drop interior detail that vanishes below 20 px. |
| **Output formats** | **SVG** canonical (recolorable, tiny). Optional **WebP** raster fallback at 2×/3× (`48px`/`72px`) for the rare non-SVG path. |
| **Text** | **Never** render text inside icons (consistent with the existing `negative_prompt`). |

This style is enforced two ways: (1) for recolored open SVGs, by **choosing source sets that already match** (line-art, 24-grid, 2px stroke — Tabler/Lucide/Material Symbols Outlined all do) and normalizing them; (2) for FAL gap-fills, by a locked **icon style prompt** + `negative_prompt` (see §6 R1) and a deterministic seed.

---

## 4. Recommended Hybrid Architecture

### 4.1 Why hybrid (not all-FAL)
All-FAL for 20k icons is *financially* possible (~$60 at $0.003/img, §7) but **fails on consistency and quality**: FLUX schnell at 2 steps produces variable line weight, occasional text artifacts, and inconsistent two-tone fills — exactly the things a brand icon set must hold constant. Open icon sets are **already** uniform line-art on a fixed grid, are free, and recolor trivially. So: **open recolored SVGs for the bulk; FAL for the gaps.**

### 4.2 Library build (offline, one-time + incremental)

**Source sets (all permissive, all recolorable):**

| Set | Count | License | Notes |
|---|---|---|---|
| **Material Symbols** | ~15,455 (all weights/styles on Iconify) / 2,500+ base | **Apache-2.0** | Outlined weight matches our line-art spec. ([Iconify](https://icon-sets.iconify.design/material-symbols/)) |
| **Material Design Icons (MDI)** | ~7,447 | **Apache-2.0** | Broad real-world object coverage. ([Iconify](https://icon-sets.iconify.design/mdi/)) |
| **Tabler Icons** | 5,500+ | **MIT**, no attribution | 24-grid, 2px stroke — *exact* match to our style. ([source](https://dev.to/icons/21-best-open-source-icon-libraries-o5n)) |
| **Phosphor** | 9,000+ (6 weights) | **MIT** | "regular"/"light" weights fit. |
| **Lucide** | 1,743 | **MIT** | Clean, consistent; great for UI/home flourishes. ([Iconify](https://icon-sets.iconify.design/lucide/)) |

> **Coverage math:** deduped concepts across these sets comfortably exceed our 20k target *before* any FAL spend. Iconify aggregates 200k+ icons across 200+ sets (mostly MIT/Apache/CC-BY-4.0), so the long tail is also covered if needed. ([Iconify](https://iconify.design/), [licensing overview](https://dev.to/usapopopooon/what-i-didnt-know-about-icon-library-licenses-and-you-might-not-either-30of))

**Pipeline (programmatic, ~Python/Node offline script — built in R1/R4, not now):**
1. **Acquire** SVGs per-set (npm `@iconify-json/*` packages or upstream repos) under their license; **record license + source per icon** in `prompt_payload`/a manifest for attribution compliance.
2. **Normalize** to the §3 spec with **SVGO**: strip hard-coded fills, set `viewBox=0 0 24 24`, normalize stroke to 2px, round caps/joins. Replace fills with `currentColor` (the well-known SVGO `convertColors`/`currentColor` plugin) so a single asset can be themed; for two-tone, map the two source colors to two CSS vars. ([SVGO recolor guide](https://svgmaker.io/blogs/how-to-batch-recolor-svg-icon-set-for-multiple-brand-themes), [currentColor SVGO plugin](https://gist.github.com/joakimriedel/b001b5bedd70274adcb6238b267565d8))
3. **Recolor** to brand tokens: emit the canonical brand variant (stroke `brand.sea.500` + fill `brand.sea.100` by default) and keep `currentColor` versions for FE theming. Batch over the whole set with the SVGO CLI + a small color-map config.
4. **Caption/tag** each icon: take the icon's existing name + category + synonyms (Iconify ships keyword aliases) → a short caption string (e.g., `"rocket spaceship launch space travel"`). This is what gets embedded for routing.
5. **Embed** each caption with the **same 384-dim model** as Q/A text (via `get_or_compute_embedding`), store the vector.
6. **Store** each finalized icon as a `MediaAsset`-style row (see §4.3) on the Azure Blob/CDN content-addressed path; `content_hash` dedups identical bytes.

### 4.3 Data model (additive, mirrors existing patterns)
Two viable options — recommend **Option A** for clean separation:

- **Option A — `icon_assets` table** (new): `id`, `content_hash` (unique), `caption`, `tags` (text[]), **`embedding Vector(384)`** with an **IVFFlat cosine index** (`USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)`, matching `topics`/`session_history` in `init.sql`), `storage_provider`, `storage_uri`, `source_set`, `license`, `palette_variant`, `evaluator_score`, `created_at`. This keeps brand icons distinct from character/synopsis media and gives them their own embedding column for the NN query. Reuses the `DualWriteResolver` for serving.
- **Option B — reuse `MediaAsset`** with a `kind='icon'` discriminator + a sibling `icon_embeddings` table (since `MediaAsset` has no `embedding` column). More reuse, slightly more join complexity.

Either way: **content-addressed storage, immutable cache, served from Blob/CDN exactly like existing media** (FE `safeImageUrl` already trusts those hosts).

### 4.4 Runtime routing (semantic nearest-neighbor) — **precomputed, zero runtime model calls**

The routing decision is made **at pack-build time**, not at quiz render time. This is the single most important load-time property.

**Build-time (in the precompute builder, `backend/app/services/precompute/builder.py` path):**
1. For each question text and each answer option text in a pack, compute its 384-dim embedding via `get_or_compute_embedding` (cached — embed once, ever).
2. **NN pick** against `icon_assets.embedding` using the existing pgvector pattern from `lookup.py::_vector_nn`: `ORDER BY embedding <=> :q LIMIT 1`, take cosine similarity.
3. **Relevance threshold τ_icon** (tuned in R2; start at the existing `0.86` analog, expect to lower toward ~0.55–0.70 for short Q/A strings — calibrate on a labeled sample). If `sim ≥ τ_icon` → bind `{question/answer → icon_asset_id, storage_uri}` into the pack JSON. **If `sim < τ_icon` → bind nothing (graceful "no icon").**
4. Optionally bind a **second-choice** icon so the FE can swap without a refetch if the first 404s (defense in depth).

**Runtime:** the pack already contains the resolved icon `storage_uri` (or none). The FE simply renders an extra small `<img loading="lazy">` (or inline sprite ref) — **no embedding, no NN, no FAL, no blocking work at request time.** This mirrors how precomputed character packs already "render instantly on a cold start."

**Why this also covers live (non-precomputed) topics:** for the live agent path, the same NN lookup can run in the **fail-open background task** (like `generate_character_images` does today) and be patched into the `session_history` JSONB via the existing `jsonb_set` persistence helpers — so it never blocks the quiz, and a returning viewer of the same session sees the icon.

### 4.5 Embedding model decision (the unblock)
- **Land a concrete 384-dim `embed_fn`** and wire it into `get_precompute_lookup` (currently `embed_fn=None`). Recommended: a local **sentence-transformers / fastembed** model that emits 384 dims (`BAAI/bge-small-en-v1.5` or `sentence-transformers/all-MiniLM-L6-v2`) so both Q/A and icon captions live in one cosine space at **$0 marginal cost** and no network hop. (If a hosted embedder is preferred, it must still output 384 dims to match the existing columns — do **not** introduce a 1536-dim model without a migration.)
- This embedder is a **shared dependency** of both the precompute topic NN (already plumbed but inert) and icon routing, so landing it pays double.

---

## 5. Character-Art Reuse (canonical topics → Q/A imagery)

Goal: pre-generate character descriptions + images for popular/canonical topics and **reuse** them as question/answer images wherever a character matches — speeding load across topics.

Grounded in the real reuse substrate (§1.5):
1. **Pre-generate canonical characters.** For a curated list of high-traffic topics (driven by `precompute/recent_topics.py` and the existing starter packs under `backend/configs/precompute/starter_packs/`), generate `Character` rows (`name`, `short_description`, `profile_text`, **`canonical_key`**, **`embedding Vector(384)`**, `image_url`, `evaluator_score`) via the existing pipeline.
2. **Dedup/reuse on `canonical_key`.** `find_character_by_canonical_key` already returns an existing `Character` and instructs callers to **skip insert + embedding + FAL**. A character that appears in many topics (e.g., a generic "wizard," "rocket," "shark") is generated **once** and its `image_url` is reused everywhere — exactly the cross-topic reuse asked for.
3. **Match Q/A → character by embedding.** When a question/answer references a known character/object, the 384-dim NN match against `Character.embedding` (same operator as icon routing) finds it; if `sim ≥ τ_char`, reuse that character's `image_url` as the Q/A image instead of (or in addition to) a brand icon. Set **τ_char in line with the existing `CROSS_PACK_MIN_COSINE = 0.85`** consistency bar so only a genuinely-matching canonical character is reused (a high bar is correct here — a wrong character is worse than a generic icon). Brand icons remain the default; character reuse is the "upgrade" when a strong canonical match exists.
4. **Reuse cache stays hot.** `generate_character_images` already HEAD-probes the existing `image_url` and skips FAL when alive — so reused canonical art costs **$0** on subsequent topics and renders instantly.
5. **Prompt-hash reuse for FAL.** `find_media_asset_by_prompt_hash` skips FAL when an identical `(prompt,provider,model)` already produced an asset above `min_evaluator_score` — so even gap-fill FAL work is deduped.

**Priority order at render time:** (a) strong canonical character match → reuse character art; else (b) brand-icon NN match ≥ τ_icon → show brand icon; else (c) no image. Brand icons are the broad baseline; character reuse is the occasional richer hit.

---

## 6. Zero-Added-Load-Time Design

The hard requirement: **never add latency to the quiz.** How each layer guarantees that:

1. **Routing is precomputed.** Icon selection happens at pack-build time (or in a fail-open background task for live topics). **Runtime adds zero embedding calls, zero NN queries, zero FAL calls.** The pack/`session_history` already carries the resolved `storage_uri` (or none).
2. **Static, content-addressed assets on CDN.** Icons live on the same Azure Blob/CDN path as existing media, served with **`public, max-age=31536000, immutable`** (already configured in `ImageStorageConfig`). First fetch is cached forever; repeat views are free. FE already trusts `blob.core.windows.net`/`azureedge.net`/`azurefd.net` in `safeImageUrl.ts`.
3. **Tiny payloads.** SVG icons target **≤ 2–4 KB** after SVGO; optional WebP raster at 48–72 px is **≤ ~2 KB**. (Compare: a 512×512 FLUX character PNG is orders of magnitude larger.)
4. **Inline SVG sprite for the fixed sets.** The home-page flourishes and any small fixed UI icon set ship as an **inlined SVG `<symbol>` sprite** (or TSX components, matching today's `frontend/src/assets/icons/` pattern) → **zero HTTP requests, zero layout shift, themed via `currentColor` + Tailwind tokens.** Q/A icons (drawn from the 20k library) load as cached `<img>`.
5. **Lazy + async + non-blocking.** Every Q/A icon `<img>` keeps `loading="lazy"` (already the norm in `AnswerTile.tsx`/`SynopsisView.tsx`/`ResultProfile.tsx`), with explicit `width`/`height` to reserve space (**zero CLS**), `decoding="async"`, and the existing skeleton→fade-in so nothing pops. Icons are **purely decorative** — the question/answer text renders and is interactive regardless of icon state.
6. **Preconnect/preload, surgically.** Add a single `<link rel="preconnect" href="https://<cdn-host>" crossorigin>` in `frontend/index.html` (today only Google Fonts is preconnected) so the first icon fetch skips DNS/TLS. **Do not `preload`** individual Q/A icons (they're below the fold / lazy) — preload is reserved only for any above-the-fold home-page hero flourish if one is added. CSP `img-src 'self' data: https:` already permits these.
7. **Fail-open everywhere.** If an icon URL 404s, `onError` falls back to nothing (or the second-choice icon) — exactly like the current `Image.tsx`/`AnswerTile.tsx` fallback to the Logo. No spinner, no blocking.

**Net effect:** the only thing added to the critical path is an *optional, lazy, cached, ≤4 KB decorative image whose URL is already in the payload.* That is zero added latency by construction.

---

## 7. Budget — $150 on FAL (including experimentation)

### 7.1 Authoritative per-image costs (cited)
- **FLUX.1 [schnell]** (the configured model): **$0.003 per megapixel, billed rounded up to the nearest MP.** A 512×512 icon (0.25 MP) and even a 1024×768 (0.786 MP) both **round up to 1 MP = $0.003/image.** ([fal.ai/models/fal-ai/flux/schnell](https://fal.ai/models/fal-ai/flux/schnell))
- **FLUX.1 [dev]** (higher fidelity, for tricky gap-fills): **$0.025 per image, flat.** ([fal docs via search](https://fal.ai/docs/documentation/model-apis/pricing); [pricepertoken](https://pricepertoken.com/image))
- Other fal models for reference: Qwen-Image **$0.02/MP**, Seedream V4 **$0.03/image**, Flux Kontext Pro **$0.04/image**. ([fal.ai/pricing](https://fal.ai/pricing))
- **Embedding cost is effectively free** and *not on FAL*: a local 384-dim sentence-transformer is $0; even if a hosted 384-dim embedder were used, OpenAI's cheapest is **$0.02 / 1M tokens** — embedding 20k captions (~5 tokens each = 100k tokens) ≈ **$0.002 total**. ([OpenAI embeddings pricing](https://tokenmix.ai/blog/openai-embedding-pricing)) *(Note: OpenAI models are 1536-dim, so for the 384-dim columns prefer the local model regardless.)*

### 7.2 Can 20k images fit $150 on FAL? — Yes, but it's the wrong spend.
- 20,000 × $0.003 (schnell) = **$60.** Technically fits.
- **But** consistency/quality for a *branded icon set* is poor at schnell-2-steps, and $60 of variable-quality auto-generated icons is worse than $0 of uniform recolored open SVGs. **So we do NOT mass-generate the library on FAL.**

### 7.3 Recommended mix & breakdown (the $150)

| Bucket | What | Volume | Unit | Cost |
|---|---|---|---|---|
| **Bulk library** | Recolored open SVGs (Material Symbols/MDI/Tabler/Phosphor/Lucide) | ~20,000 | $0 | **$0** |
| **R1 style calibration** | FLUX schnell prompt/style sweeps (icon look) | ~3,000 imgs | $0.003 | **$9** |
| **R1 fidelity spot-checks** | FLUX dev comparisons on hard icons | ~400 imgs | $0.025 | **$10** |
| **Gap-fill specialized icons** | Concepts open sets miss; schnell w/ retries | ~10,000 gen attempts (≈3–4k kept) | $0.003 | **$30** |
| **Gap-fill hard cases** | FLUX dev for icons schnell can't nail | ~800 imgs | $0.025 | **$20** |
| **Canonical character art** | Pre-gen popular-topic characters (schnell, w/ fallback ladder + null retries → ~2–3 calls each) | ~4,000 calls (≈1.5k chars) | $0.003 | **$12** |
| **R2/R3/R4 eval regen + slack** | Re-runs, A/Bs, threshold tuning, contingency | — | — | **~$25** |
| **Subtotal** | | | | **~$106** |
| **Reserve / buffer** | Unplanned experimentation | | | **~$44** |
| **TOTAL CAP** | | | | **$150** |

**Guardrail:** the repo already has `backend/app/services/precompute/cost_guard.py` + `cost.py` + `enqueue_gate.py` — wire a hard **$150 lifetime FAL ceiling** and per-round sub-budgets into the cost guard so experimentation cannot overrun. The `find_media_asset_by_prompt_hash` dedup ensures repeated prompts don't double-bill.

---

## 8. Experimentation Rounds (evidence + cost + go/no-go)

Each round produces **solid evidence** of a scalable, load-time-safe approach, has measurable success criteria, and a FAL cost that fits the $150.

### R0 — Unblock the embedder (no FAL, prerequisite)
- **Do:** Land a concrete **384-dim `embed_fn`** (local sentence-transformer) and wire it into `get_precompute_lookup` (replace `embed_fn=None`) + `get_or_compute_embedding`.
- **Evidence:** Topic vector-NN (`lookup.py`) returns non-null on a known alias-miss; embeddings persist to `embeddings_cache` with `dim=384`.
- **Cost:** $0 FAL.
- **Go/no-go:** ✅ if cached embeddings are produced and pgvector `<=>` queries run end-to-end on Postgres. **No-go ⇒ icon routing cannot proceed** (it shares this substrate).

### R1 — Style / palette calibration (small FAL)
- **Do:** (a) Recolor ~500 open SVGs through the SVGO pipeline to the §2 palette; (b) generate ~3,000 schnell + ~400 dev FAL icons against the locked icon-style prompt + `negative_prompt`, deterministic seed.
- **Evidence:** Side-by-side rendering of recolored-SVG vs FAL icons at 16–24 px; brand-color conformance check (sampled pixels within palette ΔE tolerance); style-consistency rubric scored by the existing evaluator (`precompute/evaluator.py`) or a small human pass.
- **Success criteria:** ≥ **90%** of recolored SVGs pass the brand/style rubric; FAL path establishes a prompt that hits ≥ **70%** acceptable — *and* recolored-SVG quality ≥ FAL quality at lower cost (the decision evidence for "library-first").
- **Cost:** **~$19** (≈$9 schnell + $10 dev).
- **Go/no-go:** ✅ proceed to library-first build if recolored SVGs meet the bar (expected). If not, escalate FAL share (still within budget).

### R2 — ML-routing relevance hit-rate (no/low FAL)
- **Do:** Build a **labeled sample** of ~300–500 real Q&A strings (drawn from existing starter packs + recent topics), hand-label the "correct/acceptable icon(s)." Run the NN router over the recolored library; sweep **τ_icon**.
- **Evidence:** Precision@1, "acceptable@1" (top icon is on-topic), coverage at each τ, and the false-positive rate (irrelevant icon shown). Produce a τ vs precision/coverage curve.
- **Success criteria:** At the chosen τ_icon, **precision@1 ≥ 80%** *and* **false-positive rate ≤ 5%** (a wrong icon is worse than none), with **coverage ≥ 50%** of Q&A getting an icon. Lock τ_icon.
- **Cost:** **~$0–$3** (embeddings only; FAL only if filling a few gap icons surfaced by the sample).
- **Go/no-go:** ✅ if precision and FP bars are met. No-go ⇒ raise τ (fewer, safer icons) or improve captions; re-run.

### R3 — Load-time / latency measurement (no FAL)
- **Do:** Deploy the icon-enriched quiz to a preview SWA. Measure with Lighthouse/WebPageTest + RUM: LCP, CLS, TBT, and total transferred bytes **with vs without** icons; verify icons are lazy, cached (200→304/`immutable`), and never on the critical request.
- **Evidence:** Before/after LCP & CLS deltas; network waterfall showing icons load after text/interactive; cache-hit on reload; payload size per icon.
- **Success criteria:** **LCP delta ≤ +0 ms (within noise, target ≤ +20 ms p75)**, **CLS unchanged (≤ 0.01 added)**, per-icon transfer **≤ 4 KB**, and **no icon request blocks** first interaction. This is the literal "zero added load time" proof.
- **Cost:** $0 FAL.
- **Go/no-go:** ✅ only if LCP/CLS are within thresholds. No-go ⇒ shrink/inline assets, defer further, fix preconnect.

### R4 — Library coverage % (recolored vs FAL gap)
- **Do:** Run the router over a large corpus (all starter-pack Q&A + a broad topic sweep, thousands of strings) against the full recolored library. Measure what fraction get a **good** icon (≥ τ_icon AND passes spot-check) from the **recolored library alone**, vs what needs **FAL gap-fill**. Generate the identified gaps on FAL.
- **Evidence:** Coverage histogram by topic category; ranked list of missing concepts; count of FAL gap icons actually needed (drives the §7 gap-fill bucket); post-gap-fill coverage.
- **Success criteria:** **≥ 85%** of real Q&A get a good icon from the **recolored open library with $0 FAL**; total FAL gap-fill needed **≤ the §7 buckets (≤ ~5k kept images, ≤ ~$50)**; post-gap-fill coverage **≥ 95%** of *eligible* Q&A (those that warrant an icon).
- **Cost:** **~$30 schnell + ~$20 dev = ~$50** (the gap-fill buckets).
- **Go/no-go:** ✅ ship if library covers ≥ 85% at $0 and total FAL stays under budget. No-go ⇒ expand source sets (Iconify long tail) before spending more on FAL.

**Total planned FAL across R1–R4 + production gap-fill + character art ≈ $106, with ~$44 reserve inside the $150 cap** (enforced by `cost_guard`).

---

## 9. Open Questions / Risks
- **Embedder choice (R0)** is the critical-path unblock and must emit **384 dims**; a 1536-dim model would require a migration of every `Vector(384)` column — avoid.
- **Short-string embedding quality:** Q/A strings are short; caption engineering (synonyms/aliases from Iconify) and τ tuning (R2) matter more than model size. Validate FP rate hard — a wrong icon is worse than none.
- **License hygiene:** record source set + license per icon (Apache-2.0 attribution is "welcomed not required" for Material Symbols; MIT needs the license text retained). Keep a generated `THIRD-PARTY-ICONS.md` manifest. *(Authoring note: not created here — to be produced by the build pipeline in R1/R4.)*
- **`MediaAsset` has no `embedding` column** → either add `icon_assets` (Option A, recommended) or a sibling embedding table (Option B).
- **Two-tone via `currentColor`** only carries one color; for genuine two-tone, use two CSS custom properties or ship the pre-baked brand variant as the served asset (recommended for the `<img>` path) and reserve `currentColor` for inlined sprite icons.

---

## 10. Sources
- FAL FLUX schnell pricing ($0.003/MP, round up): https://fal.ai/models/fal-ai/flux/schnell
- FAL pricing table (Qwen $0.02/MP, Seedream $0.03/img, Kontext Pro $0.04/img): https://fal.ai/pricing
- FAL FLUX dev ($0.025/image): https://fal.ai/docs/documentation/model-apis/pricing , https://pricepertoken.com/image
- Open icon libraries & licenses (Lucide/Tabler/Phosphor/Material Symbols/MDI, MIT/Apache-2.0): https://dev.to/icons/21-best-open-source-icon-libraries-o5n , https://icon-sets.iconify.design/material-symbols/ , https://icon-sets.iconify.design/mdi/ , https://icon-sets.iconify.design/lucide/
- Icon license nuances: https://dev.to/usapopopooon/what-i-didnt-know-about-icon-library-licenses-and-you-might-not-either-30of
- Iconify aggregate (200k+ icons, 200+ sets): https://iconify.design/
- Batch SVG recolor with SVGO / currentColor: https://svgmaker.io/blogs/how-to-batch-recolor-svg-icon-set-for-multiple-brand-themes , https://gist.github.com/joakimriedel/b001b5bedd70274adcb6238b267565d8
- OpenAI embeddings pricing (context only; note 1536-dim mismatch): https://tokenmix.ai/blog/openai-embedding-pricing

---

## Adversarial Skeptical Review

**Reviewer:** read-only adversarial pass, 2026-06-29. Verified every load-bearing claim against the actual repo code and re-checked all external facts via web search (citations inline). Verdict legend: **HOLDS** / **OVERSTATED** / **WRONG**.

### A. Per-claim verdicts

#### A.1 COST / the $150 budget

- **FLUX schnell = $0.003/MP, billed rounded up to nearest MP → $0.003 per 512×512 icon — HOLDS.** Confirmed on fal's own model page and two independent secondaries (a 512×512 = 0.26 MP rounds up to 1 MP = $0.003). Sources: [fal.ai schnell](https://fal.ai/models/fal-ai/flux/schnell), [pixazo breakdown](https://www.pixazo.ai/blog/flux-schnell-api-cheapest-pricing).
- **"FLUX dev = $0.025 per image, flat" (§7.1, §10) — WRONG (factual error).** fal's official FLUX.1 [dev] page states **"$0.025 per *megapixel*, rounded up to the nearest megapixel"** — NOT a flat per-image rate. Source: [fal.ai/models/fal-ai/flux/dev](https://fal.ai/models/fal-ai/flux/dev). For a ≤1 MP icon the per-unit cost coincides ($0.025), so the **icon-bucket arithmetic survives**, but the error is real and bites on any >1 MP dev render (e.g. a 1536-wide hero or a 1024×1024 portrait = 1 MP is fine, but 1280×1024 = 1.3 MP → billed 2 MP = $0.05). Fix the unit in §7.1/§10 before anyone sizes a dev image > 1 MP.
- **The $106 estimate is OPTIMISTIC on volume accounting, not on unit price.** The schnell unit cost is honest. What is soft:
  - **Gap-fill "≈10,000 gen attempts (≈3–4k kept) = $30."** You pay for *every* attempt, not just kept ones. With the existing **null-retry (up to `max_attempts`-1 re-issues, clamped to 3)** *and* the **3-rung branded fallback ladder** in `image_pipeline.py::_generate_character_with_brand_fallback`, a single "image" can cost **1–6 `generate()` calls**. The plan's "≈2–3 calls each" for character art is plausible *only* for non-branded archetypes; branded/IP characters (which is exactly where schnell struggles and the ladder fires) can hit 4–6 calls. Real worst case on the character bucket is closer to **$25–$35, not $12**, and gap-fill closer to **$45–$60, not $30**.
  - **No measured keep-rate.** "≈3–4k kept of 10k" is a guess with zero evidence behind it; if schnell's icon keep-rate is 20% instead of 35%, attempt volume to hit the same kept count roughly doubles.
- **THE BUDGET-ENFORCEMENT CLAIM IS WRONG — this is the most dangerous line in the plan.** §7.3 says: *"the repo already has `cost_guard.py` + `cost.py` + `enqueue_gate.py` — wire a hard $150 lifetime FAL ceiling into the cost guard."* Verified in code: **`cost_guard.py` is a DAILY budget guard that sums `precompute_jobs.cost_cents`** (the LLM *text*-generation spend inside the `run_build` state machine). **`cost.py` aggregates the same `precompute_jobs.cost_cents`.** **FAL image spend is NEVER recorded in `precompute_jobs.cost_cents`** — image generation runs in fire-and-forget FastAPI `BackgroundTasks` in `image_pipeline.py`, entirely outside the build state machine, and writes nothing to any cost ledger. There is **no FAL cost ledger anywhere in the repo**, no lifetime accumulator, and `precompute_jobs.cost_cents` is a **`SMALLINT` (max $327.67)** that overflows long before a lifetime cap would be meaningful. So "the cost guard prevents overrun" is **infrastructure that does not exist** and is non-trivial new work (a real $-ledger that the FAL client writes to + a lifetime accumulator table). As written, **nothing in the running system would stop experimentation from blowing past $150.** This must be built before any R1+ FAL spend, not assumed.
- **What blows the budget:** (1) the missing ledger means there is literally no brake; (2) retry/fallback call-multiplication on hard icons; (3) an unmeasured keep-rate; (4) any decision to A/B at higher resolution or on dev for "polish." Verdict: **unit math HOLDS, total estimate OVERSTATED-optimistic, enforcement claim WRONG.**

#### A.2 LIBRARY / LICENSING

- **Material Symbols = Apache-2.0, recolor + redistribute in a commercial app = permitted — HOLDS.** Confirmed: Apache-2.0, remix/re-share allowed, attribution "appreciated, not required." Sources: [google/material-design-icons LICENSE](https://github.com/google/material-design-icons/blob/master/LICENSE), [Material Icons guide](https://developers.google.com/fonts/docs/material_icons). Apache-2.0 carries a real obligation the plan under-states: you must **retain the license text and a NOTICE if present, and state changes** — for *recolored/normalized* SVGs you are creating derivative works and must preserve the notice. The plan's "attribution welcomed not required" gloss (§9) is right for *attribution* but glosses the **mandatory license-retention** clause.
- **Tabler / Phosphor / Lucide = MIT, recolor + redistribute = permitted — HOLDS**, but MIT **requires the copyright + permission notice be retained** in distributed copies. The plan acknowledges this in §9 ("MIT needs the license text retained") — good. MDI (Pictogrammers) is Apache-2.0 — HOLDS.
- **Icon COUNTS are OVERSTATED via double-counting.** The "~20,000 DISTINCT useful icons" target conflates raw variant counts with distinct concepts:
  - **Phosphor "9,000+" is ~1,500 concepts × 6 weights** (Thin/Light/Regular/Bold/Fill/Duotone). You will use *one* weight to match the §3 line-art spec, so Phosphor contributes **~1,500 distinct**, not 9,000. Source: [phosphoricons.com](https://phosphoricons.com/).
  - **Lucide is ~1,650–1,743**, fine (plan's 1,743 matches Iconify) — but Lucide is a Feather fork with **heavy concept overlap** with Tabler.
  - **Material Symbols "~15,455 on Iconify" is mostly weight/fill/grade *axis* permutations of ~2,500 base glyphs** — the plan even prints "2,500+ base," then quietly uses the 15k number for coverage comfort.
  - **Cross-set dedup is severe:** Tabler, Lucide, Feather, Material, MDI, Phosphor all redraw the same universal concepts (home, search, user, heart, star, arrow, gear…). The realistic count of **distinct useful concepts after dedup is ~6,000–10,000, not 20,000.** That is still likely *enough* for quiz Q/A (most quiz nouns are common objects), but the "comfortably exceed 20k before any FAL" claim is **OVERSTATED**. The honest framing: "enough distinct concepts to cover the common long tail; rare/branded concepts go to FAL." R4 is where this gets proven — see below.
- **Programmatic recolor producing on-brand, consistent results at scale — HOLDS WITH CAVEATS.** `currentColor` + SVGO recolor is a real, well-trodden technique. But the plan's own §10.5 / §3 admit the hard part: **`currentColor` carries exactly ONE color**, so genuine *two-tone* brand icons cannot be expressed by a single `currentColor` swap — you must either ship pre-baked two-color SVGs (then they are NOT themeable per surface and you need a light + dark variant each, ~2× the asset count) or inject two CSS custom properties (only works for *inlined* SVG, not the `<img>` path the Q/A icons use). **The `<img>`-served Q/A icons therefore cannot be live-themed for dark mode** — they are baked. This collides with the dark-mode requirement (the app ships `prefers-color-scheme: dark`, see `index.html` theme-color). Not fatal, but the plan's "recolor trivially / themed via currentColor" is **OVERSTATED for the actual `<img>` delivery path**.

#### A.3 ML ROUTING (semantic NN over icon captions)

- **`embed_fn=None` is a HARD blocker — HOLDS, confirmed in code.** `dependencies.py::get_precompute_lookup` wires `embed_fn=None` (line ~321); `lookup.py::resolve_topic` skips the vector path entirely when `_embed_fn is None`. So **vector NN is genuinely inert today**, exactly as the plan says. R0 (land a 384-dim `embed_fn`, wrap `get_or_compute_embedding`) does unblock it — **HOLDS**. The wiring is small and the plan describes it correctly.
- **"precision@1 ≥ 80% / FP ≤ 5% for arbitrary Q&A" — OVERSTATED / unproven, and this is the central technical risk.** Three compounding problems:
  1. **Asymmetric retrieval in a symmetric model.** The plan reuses the *same* 384-dim sentence model for both the **query** (a short Q/A string like "Which planet is closest to the Sun?") and the **document** (an icon caption like "rocket spaceship launch space travel"). `all-MiniLM-L6-v2` / `bge-small` are trained for **symmetric sentence-similarity**, not **query→keyword-caption asymmetric retrieval**. A question about Mercury will not embed near a *planet* icon caption unless the caption literally contains the right nouns; the *answer* "Mercury" might match a *thermometer* or the *car brand* instead. External evidence: MiniLM-L6-v2 retrieves only **39.4% of gold evidence in top-10** and shows recall ~0.22 in one study — fine for autocomplete, weak for precise routing. Source: [arxiv 2409.17383](https://arxiv.org/pdf/2409.17383), [MiniLM card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
  2. **Caption quality IS the hidden failure point — the plan correctly names it but under-weights it.** Routing precision is bounded by caption quality, not model size. Iconify aliases are sparse and inconsistent ("mdi:rocket-launch" → maybe "rocket, launch" and nothing about "space/NASA/astronaut/orbit"). Garbage captions → garbage NN, regardless of τ. **Caption generation is itself an LLM job with its own cost/quality that the plan does not budget or test.**
  3. **The 0.86 anchor is from a *different distribution*.** `LookupThresholds.match = 0.86` was tuned for **topic-name → topic-name** matching (alias misses), a near-symmetric short-to-short task. The plan correctly notes it must drop toward ~0.55–0.70 for Q/A→caption — but that is a *guess*, and the lower you push τ the worse FP gets. The "FP ≤ 5% AND precision@1 ≥ 80% AND coverage ≥ 50%" triple is **three constraints on one knob** and may be jointly unsatisfiable for short asymmetric strings.
  - **The no-icon fallback is sound — HOLDS.** "below τ → bind nothing" is the right default and is genuinely graceful. The architecture *degrades safely*; the open question is whether it degrades to "no icon" so often that the feature is pointless (low coverage) or shows wrong icons (high FP). **Both failure modes are invisible until R2 measures them on a real labeled set — and R2 as written may declare success on too small / too easy a sample.**

#### A.4 LOAD-TIME (the core promise)

- **"Precompute the NN at pack-build, serve a cached ≤4 KB lazy `<img>` whose URL is already in the payload" — HOLDS *in principle*, but the build-time path it relies on DOES NOT EXIST yet.** Verified: `builder.py::run_build` has **zero image/icon/asset handling** (grep for `image|asset|icon|question` in builder.py: no matches in the orchestrator body). There is **no per-question `image_asset_id` population path** in the builder today; the `questions.image_asset_id` / `synopses.image_asset_id` columns exist in `init.sql` but are unwired. So "the pack already contains the resolved icon URL" is **aspirational** — it requires new build-time code that runs the NN and writes the binding. The *design* is airtight; the *claim that it leverages existing infra* is OVERSTATED — almost all of it is net-new.
- **CDN trust in `safeImageUrl.ts` — HOLDS.** Confirmed: `blob.core.windows.net`, `azureedge.net`, `azurefd.net`, `azurestaticapps.net` are all in `DEFAULT_ALLOWLIST`. Azure Blob/CDN icon URLs will pass.
- **`cache_control: public, max-age=31536000, immutable` — HOLDS** (in `ImageStorageConfig`). Content-addressed + immutable is correct.
- **"onError falls back to nothing, no broken icon" — WRONG for the component the plan cites.** The plan (§6.7, §1.6) leans on `frontend/src/components/common/Image.tsx` as the "fail-open onError fallback." **That component does the OPPOSITE:** on error it sets `src = "https://placehold.co/600x400/..."` — an **external host that is NOT on the `safeImageUrl` allowlist**, adding a **cross-origin request to a third-party placeholder service** and rendering a visible "Image Not Found" box. That is neither fail-open-to-nothing nor zero-added-request. (`AnswerTile.tsx` *does* fail open correctly — it falls back to the `<Logo>` and never refetches — so the safe pattern exists, but the plan cites the wrong component as the template.) If Q/A icons are rendered through `Image.tsx`, a 404 storm on cold CDN = a burst of `placehold.co` requests. **Fix: route icons through the AnswerTile-style fallback, never `Image.tsx`.**
- **CLS — UNDER-VERIFIED and there is a real risk at the integration point.** `AnswerTile.tsx`'s `<img>` has **no explicit `width`/`height` attributes** — it relies on a fixed `h-32 w-full` *container*, which does reserve space, so answer-tile CLS is probably OK. BUT: **`QuestionView.tsx` has NO image slot at all today** — the question is a bare `<h2>`. Adding a question icon means inserting a new element next to/above the heading; if it is not given reserved dimensions it **will** shift the heading and answer grid on load. The plan's §6.5 promise of explicit `width`/`height` is **not reflected in any existing code** and must be enforced at the (not-yet-written) integration point. R3 measures CLS *after* the fact; it does not guarantee the integration is built CLS-safe.
- **Collision the plan never addresses: answer tiles ALREADY render a 128px-tall image** (`answer.imageUrl` → the FAL character/answer art). Adding a brand *icon* to an answer is now a **second image per tile** (or a contested slot). The plan never says whether the icon *replaces*, *augments*, or *competes with* the existing answer image — and "two images per answer tile" is both a layout problem and a load-time problem (more bytes, more requests) that contradicts the "purely decorative, zero-added" framing.
- **Net load-time verdict:** the *physics* of "static, immutable, lazy, tiny, URL-in-payload" is sound and genuinely can be zero-added-latency. But the claim rests on (a) build-time code that doesn't exist, (b) a fallback component cited that actually adds a cross-origin request, (c) a CLS guarantee not present at the real integration points, and (d) an unaddressed double-image collision on answer tiles. **HOLDS as a design target; OVERSTATED as "leverages what's already there."**

#### A.5 Character-art reuse coherence (§5)

- **Reusing canonical character art as a Q/A icon WILL CLASH — the plan contradicts itself.** §3 defines the icon language as **two-tone flat line-art, ≤2 brand colors, transparent bg**. But character art is generated with `STYLE_ANCHOR = "unified illustrated quiz art style, single consistent palette, matching brushwork"` (verified in `image_tools.py`) — i.e. **full-color illustration**, explicitly NOT brand-two-tone (§3 itself says "Never full-color illustration — that is reserved for character art"). Yet §5's render priority is **(a) character art FIRST, then (b) brand icon**. So the *preferred* asset is the one that breaks the icon style system. Dropping a full-color illustrated wizard next to a row of two-tone sea-blue line icons is **visually incoherent**, and character art is **not brand-colored**, so it cannot be made consistent by recolor. Verdict: **§5 reuse-as-Q/A-icon is OVERSTATED/incoherent as specced.** Character art may belong on the *result/synopsis* hero surfaces (where it already lives) but should **not** be mixed into the line-icon Q/A row. The priority ladder is backwards for visual consistency.

### B. TOP RISKS (ranked)

1. **No FAL cost ledger exists; the "cost guard enforces $150" claim is false.** Nothing in the running system records or caps FAL image spend. Highest risk because it is a *money* risk presented as already-solved. **Must build a real $-ledger + lifetime accumulator before any FAL spend.**
2. **ML-routing precision on short asymmetric Q/A→caption strings is unproven and likely below the 80%/≤5%-FP bar** with a symmetric 384-dim mini model + sparse Iconify captions. This is the feature's whole point; if it routes noise, the feature is wrong-icon-or-nothing.
3. **Caption quality is the silent precision ceiling and is un-budgeted/un-tested.** No plan for generating, cost-bounding, or QA-ing the ~6–10k captions the router depends on.
4. **"Leverages existing infra" is largely aspirational.** Build-time icon binding (`builder.py`), the FAL cost ledger, an `icon_assets` table + IVFFlat index, and a 384-dim `embed_fn` are ALL net-new. The plan reads as lower-effort than it is. (`embeddings_cache` has **no** vector index today — only `topics`/`session_history` do — so the icon table's ANN index is genuinely new work, correctly noted but easy to under-scope.)
5. **`Image.tsx` fallback adds a cross-origin `placehold.co` request** — directly contradicts "zero added request / fail-open to nothing." Easy fix, but cited as proof of a property it disproves.
6. **Distinct-concept coverage (~6–10k after dedup, not 20k)** + dark-mode two-tone delivery via baked `<img>` (no live theming). Coverage is probably *sufficient* but the headline number is inflated.
7. **CLS / double-image collision at the not-yet-built integration points** (`QuestionView` has no image slot; `AnswerTile` already has one image). Sizing + slot semantics undefined.
8. **FLUX dev unit is mislabeled** ($/MP not $/image) — minor for ≤1 MP icons, real for larger renders.

### C. What each experimentation round MUST ADD to give SOLID evidence

- **R0 (embedder):** ADD a held-out **asymmetric retrieval probe**, not just "NN returns non-null." Embed ~50 real Q/A strings, retrieve from a small captioned icon set, and report precision@1 *before* committing to library-first — if the symmetric model can't do query→caption, the whole router is at risk and you want to know in R0, cheaply. Also pin the **exact model + revision hash** in config (reproducibility).
- **R1 (style/palette):** ADD (1) **dark-mode + contrast judging** (every icon rendered on light AND dark surface, WCAG non-text contrast ≥ 3:1 against both); (2) a **two-tone delivery decision recorded with evidence** (baked `<img>` vs inlined sprite) — prove the `<img>` path's dark-mode story; (3) **license-manifest generation as an acceptance gate** (THIRD-PARTY-ICONS.md auto-built, Apache NOTICE retained), not deferred prose. Don't accept "90% pass rubric" judged by the *same* evaluator that scores characters — use a separate icon rubric + a human spot-check, and **publish inter-rater agreement**.
- **R2 (routing hit-rate):** ADD (1) a **bigger, harder, stratified** labeled set (≥1,000 Q/A, stratified by topic category AND by string length AND by "abstract vs concrete noun"), hand-labeled by ≥2 people with agreement reported — 300–500 is too small and too easy to game; (2) **separate query types**: question-stem routing vs answer-option routing are different distributions, measure both; (3) report the **full τ vs (precision, coverage, FP) curve** and prove a τ exists satisfying all three bars *simultaneously* — if none does, the feature ships at low coverage by design, say so; (4) **ablate caption quality** (Iconify-alias-only vs LLM-enriched captions) so the caption-cost decision is evidence-based; (5) include **adversarial near-miss pairs** (Mercury planet vs Mercury element vs Mercury car) to stress FP.
- **R3 (load-time):** ADD (1) measurement at the **real, modified `QuestionView`/`AnswerTile`**, not a synthetic page — including the **question-heading insertion point** and the **answer double-image** case; (2) **CLS measured with reserved-dimension enforcement on AND off** to prove the sizing actually prevents shift; (3) a **404/cold-CDN test** that confirms the fallback path makes **zero cross-origin requests** (i.e. that icons do NOT go through `Image.tsx`); (4) p75/p95 RUM on real devices/networks, not just a single Lighthouse run; (5) **total-bytes and request-count deltas** explicitly (the double-image concern).
- **R4 (coverage):** ADD (1) **distinct-concept coverage after cross-set dedup** reported as a number, to replace the inflated 20k headline; (2) coverage measured **at the locked τ from R2 with the FP bar held** (coverage at FP≤5%, not raw NN coverage); (3) **a hard FAL-ledger dollar readout** from the new cost ledger proving spend tracked correctly end-to-end (this also exercises Risk #1's fix); (4) a **keep-rate measurement** for gap-fill so the §7 attempt-volume guess is replaced with data.
- **MISSING across all rounds and MUST be added somewhere:**
  - **Accessibility / alt-text policy for decorative-vs-meaningful icons.** The plan calls icons "purely decorative" but `AnswerTile` sets a *meaningful* `alt` (`Image for: {text}`). Decide per-surface: decorative icons need `alt=""` + `aria-hidden`; meaningful ones need real alt. Untested today and a real a11y regression risk.
  - **Build-time routing cost at 20k scale.** Running an NN per Q/A per pack across the full library at build time has a real CPU/latency cost (IVFFlat with lists=100 over tens of thousands of vectors, per string, per pack). Measure build-time throughput; the plan asserts "zero *runtime*" but never bounds *build-time*.
  - **A visual-coherence test for §5 character-art-as-icon** — render a real Q/A row mixing full-color character art with two-tone line icons and have it judged. Expectation per this review: it fails; demote §5 to hero surfaces only.
  - **Caption generation cost line in §7** (currently $0; it is not free if LLM-enriched).

### D. GO / NO-GO

**NO-GO as written** for "this plan, as specified, will *prove* a scalable, zero-added-load-time approach." It is a strong, well-grounded *design* and the load-time *physics* are achievable — but four load-bearing claims do not survive scrutiny against the actual code/facts: (1) the **cost guard does not and cannot track FAL spend** (no ledger exists), so the budget is unenforced; (2) the **routing precision bar is unproven and at real risk** for short asymmetric Q/A→caption matching with the chosen model class + sparse captions; (3) the **cited fail-open component (`Image.tsx`) adds a cross-origin request**, contradicting the zero-request promise; (4) **§5 character-art reuse is visually incoherent** with the §3 icon system and is prioritized backwards. The experimentation rounds are **directionally right but not yet sufficient** — they measure the easy properties (unit cost, recolor quality, post-hoc LCP) and under-test the three things most likely to fail (asymmetric routing precision on a hard stratified set, build-time-binding + cost-ledger that don't exist yet, and CLS/double-image at the real integration points).

**Path to GO:** (a) build a real FAL $-ledger + lifetime cap and prove it in R4; (b) move the asymmetric-routing precision probe earlier (R0) and harden R2's labeled set + τ-triple feasibility + caption ablation; (c) re-spec the FE fallback to the `AnswerTile` pattern and forbid `Image.tsx` for icons; (d) demote §5 character-art reuse out of the Q/A icon row; (e) re-label distinct-concept coverage honestly and bound build-time routing cost. With those, the plan becomes GO-able — the underlying architecture is sound; the *evidence* and a few *grounding* claims are not yet there.
