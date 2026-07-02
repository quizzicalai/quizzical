# Pre-computed content plan (2026-07-02)

Grounded in a **live read-only prod snapshot** (not the stale "only 5 seeded" assumption):

| Metric | Prod value (2026-07-02) |
|---|---|
| `topics` | 947 |
| `topic_packs` (all `status='published'`) | **957** |
| `characters` | 3189 |
| characters **with** `image_url` | **1573 (~49%)** |
| characters **missing** an image | **~1616 (~51%)** |
| `media_assets` (local rehost) | **0** — images are served straight from FAL CDN URLs |
| `session_history` (real quizzes taken) | 137 |
| Q&A/answer-option images | none (feature flag `qa_generated_images_enabled` OFF) |

**Takeaways that reframe the work:**
1. **Deploying "what we have" is essentially DONE** — 957 packs are already published in prod (the growth/seed machinery ran). The plan is now about *quality, coverage, and durability*, not initial seeding.
2. **Biggest coverage gap = images**: ~1616 characters (51%) render text-only on the instant path.
3. **Durability risk**: `media_assets=0` means every precomputed image is a live FAL CDN URL. If FAL rotates/expires those URLs, images 404. Local rehost (bytes_blob) exists in code but has never run.
4. **Staleness risk (your FAL-model note)**: some packs/images predate the recent quality changes (flux/dev for hero images, gpt-4o-mini for profiles). Content generated on the old models should be re-judged and selectively regenerated.

## Assets & levers (already in the repo)
- Generate/judge topics: `backend/scripts/generate_ranked_pack_candidates.py` (Gemini judge).
- Generate images for packs: `backend/scripts/generate_images_for_packs.py`; backfill misses: `backend/scripts/backfill_images_for_batches.py` (FAL + Gemini describer).
- Build+sign archives: `backend/scripts/build_starter_packs.py` (needs `PRECOMPUTE_HMAC_SECRET`).
- Seed to prod: `seed-prod-packs.yml` (archive_glob) or `POST /api/v1/admin/precompute/import` (needs `OPERATOR_TOKEN` + `X-Archive-Signature`).
- Evaluate: `eval_resolution.py` (topic→outcome routing, offline, $0 — currently 94/94), `eval_canonical_correctness.py` (`--no-judge` offline $0; `--judge` = Gemini spend), image vision-judge `eval_image_quality.py` (vision key).
- Cost guardrail: `fal_spend_ledger` + `fal_spend_counter` tables enforce a lifetime FAL cap (`settings.images.fal_budget`, ~$150).

## Phased plan

### Phase 0 — Measure before spending (FREE, do now)
- Run `eval_resolution.py` (offline) to confirm routing still 94/94 after all the merges.
- Run `eval_canonical_correctness.py --no-judge` over prod packs (offline) to catch structural/canonical regressions for free.
- Audit image coverage per pack (which topics are most-served yet imageless) to prioritize the paid backfill by impact, not batch order.
- **Output:** a prioritized backfill list + a "needs-regen" list. No spend.

### Phase 1 — Durability: locally rehost existing FAL images ($0 FAL, some egress)
`media_assets=0` is a latent outage. Rehost the 1573 existing FAL image URLs into `media_assets.bytes_blob` (served via `/media/{id}` with immutable cache) so images survive FAL URL expiry. This is a download+store, not generation — no FAL generation cost. **Highest reliability ROI.**

### Phase 2 — Image backfill for the ~1616 imageless characters (FAL spend)
Run `backfill_images_for_batches.py` in **bounded batches**, prioritized by Phase-0 impact, each gated by the `fal_spend_ledger` cap. Estimate: at ~$0.011/small image, ~1616 images ≈ **~$18** (well under the ~$150 lifetime cap), plus a small Gemini cost for physical descriptions. Verify a sample renders (naturalWidth>0) after each batch; then rebuild+sign+re-seed those packs.

### Phase 3 — Re-eval after model changes; regenerate below-bar (Gemini/vision spend)
For packs/images produced before flux/dev + gpt-4o-mini: sample-judge with `eval_canonical_correctness --judge` (text) and `eval_image_quality` (images). Regenerate only what's below bar. Keeps spend proportional to actual staleness.

### Phase 4 — Grow topics (Gemini + FAL spend)
Add the highest-intent still-missing precomputed packs (personality frameworks users type most): **MBTI, Enneagram, DISC, Big Five/OCEAN, Attachment Styles, Love Languages, Zodiac, Chinese Zodiac, Tarot** — generate → judge (gate on pass) → image → build/sign → seed.

### Phase 5 — Answer-option ("Q&A") images (FAL spend + relevance gate)
Pre-compute images for answer options behind `qa_generated_images_enabled`. Re-validate the relevance gate (precision was 1.0) so we never spend on irrelevant images, enforce all-or-none at the grid (already shipped in PR #52), then flip the flag after a real-FAL validation pass.

## Spend posture
All generation is gated by the `fal_spend_ledger` lifetime cap. I'll run Phases 0–1 now ($0 generation). Phases 2–5 spend real FAL/Gemini money — I'll run them in bounded batches with cost reported per batch, on your go (you said "continue to pre-compute as much as possible" — I'll proceed but keep each batch bounded + verified rather than one big blind spend).

## Progress
- 2026-07-02: plan written from live prod snapshot. Phase 0 starting.
- 2026-07-02: **Phase 0 done (free).** `eval_resolution` = **94/94** (routing solid post-merge). Budget/coverage audit:
  - **FAL spend ledger is EMPTY (0 rows)** → the 1573 existing images were generated before the ledger guardrail was wired; the ~$150 lifetime cap has full headroom, but the guardrail only protects *future* ledger-routed generation.
  - **All 1573 images are ephemeral FAL CDN URLs** (`v3b.fal.media`), and **`media_assets=0`** (no local rehost). **→ Durability is the #1 precompute risk**: if FAL rotates/expires those URLs, ~half the cast art 404s. The `characters.image_asset_id` column exists (rehost hook) but is unused.
  - Imageless = **1616** characters (mix of real characters + generic types like "Doctor"/"President").
- **Revised sequencing:** Phase 1 (rehost the 1573 existing images → durability, $0 FAL) BEFORE Phase 2 (backfill the 1616 missing → ~$18 FAL). Both are real prod-mutating batch ops → run monitored, on owner go, in bounded batches with per-batch cost reported. Phase 0 artifacts + this durability finding are the actionable output; paid/large phases are staged and costed, ready to run.
