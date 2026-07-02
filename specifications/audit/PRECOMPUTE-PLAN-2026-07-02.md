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

### Phase 5 progress — answer-option images (2026-07-02, feat/answer-images-ship)

<!-- Appended subsection owned by the answer-images workstream; other agents edit sections above. -->

**Done — pool built, real-FAL validated, flags flipped in the PR:**
- New pool builder: `backend/scripts/generate_answer_images.py`. For a curated 25-pack high-intent slice it runs the QUESTION-level relevance gate (bge-small, margin=0.04 / floor=0.20 / question_min_fraction=0.25), generates ONLY gate-passing questions through `FalLedger.guarded_generate`, enforces all-or-none per question at persist time, rehosts bytes into `media_assets.bytes_blob`, and writes durable `GET /api/v1/media/{id}` URLs into `questions.options.items[*].image_url` (the exact JSON the hydrator → `/quiz/status` serves). Idempotent; `--dry-run` gate preview; `--budget-usd` per-run stop on top of the lifetime ledger cap.
- **Gate stats (prod, 25 packs / 125 baseline questions):** 26 questions cleared (21%), 99 blocked (never spent). 14 topics now carry answer images (hogwarts-house, disney-princess, pokemon-starter, pokemon-type, friends-character, lord-of-the-rings-race, avengers-original-six, greek-god, norse-deity, ancient-egyptian-god, legend-of-zelda-race, coffee-personality, classic-cocktail-style, dessert-personality); 11 packs cleared nothing and stay text-only (never placeholders).
- **Spend:** 121 images (512×512 schnell, 4 steps) = **$0.096 total**, all 121 ledger-recorded (`fal_spend_ledger` now live in prod). Well under the $10 task cap and the $150 lifetime cap.
- **Durability:** all 121 assets rehosted (`bytes_blob` 100%); zero FAL CDN URLs are user-visible on the answer path; live probes of `/api/v1/media/{id}` return 200 `image/jpeg` with immutable cache.
- **All-or-none verified in prod SQL:** 26 questions fully imaged, 0 partially imaged, 4619 text-only.
- Flags `quizzical.images.qa_icons_enabled` + `qa_generated_images_enabled` flipped ON in `backend/appconfig.local.yaml`; FE gate `features.qaImages` now advertised by `/config` (unit-tested). FE `safeImageUrl` allowlist extended with `azurecontainerapps.io` so the API-host media URLs render.
- Remaining for later passes: widen the slice beyond the curated 25 packs (whole 957-pack catalog ≈ same per-pack ~$0.004 economics), and optionally surface question-STEM images through the hydrator (`stem_images` currently off — the serve path only renders per-option images).

### 2026-07-02 (later) — Phases 1-3 EXECUTED + object-vs-person prompt fix (branch `feat/precompute-content-ops`)

**Phase 1 — rehost (DONE, $0 FAL).** Built the missing producer, `backend/scripts/rehost_fal_images.py` (SSRF-guarded downloads, sha256 `content_hash` dedup, resumable, `--limit/--batch-size/--dry-run`; original FAL URL preserved in `media_assets.prompt_payload.rehost.source_url` for rollback). All **1589** live `v3b.fal.media` character images were downloaded into `media_assets.bytes_blob` and `characters.image_url` REWRITTEN to the durable API URL (`…azurecontainerapps.io/api/v1/media/{id}`); `characters.image_asset_id` linked; 119 `session_history.character_set` snapshots mirrored (name-scoped jsonb, same shape as `image_pipeline`). 0 dead URLs, 0 errors. NOTE: the served route is `/api/v1/media/{id}` (prod `api_prefix=/api/v1`), not the `/api/media/{id}` in `local_provider.LOCAL_URI_FMT`. FE: `azurecontainerapps.io` added to the `safeImageUrl` allowlist (same breadth as the existing Azure entries; FE CSP `img-src https:` already allows it). Importer guard added in `pack_importer._upsert_characters_and_collect_ids`: re-seeding an OLD archive can no longer clobber a rehosted URL with its stale pre-rehost `fal.media` source (fresh regenerated art still wins) — unit-tested.

**Object-vs-person image-prompt fix (the "banh mi → photo of a person" complaint).** Vision-judge sample of 100 prod images (gemini-flash, ~$1 est): overall pass **46%**; in the food/object slice **24 of 28 fails depicted a PERSON** ("The Margarita" → woman next to a cocktail; "Tokyo" → woman in kimono; "The Orchid" → woman). Root cause: every builder framed outcomes as portraits ("Portrait of X", portrait style-suffix, face-quality tokens). Fix (deterministic, no LLM, hot-path safe): `image_tools.infer_subject_kind` (person-words checked first; strong-object compounds like "skincare hero ingredient" override; unknown ⇒ person = zero regression) now routes `build_character_image_prompt`, `build_branded_attempt_prompt`, `build_descriptive_attempt_prompt`, and `build_result_image_prompt` (no face tokens on objects) to item-itself framing (`OBJECT_STYLE_SUFFIX`, people-suppressing negatives); `character_describer.describe_character_physically` gained `subject_kind="object"` instruction sets; wired through the branded ladder in `image_pipeline`. 37 new unit tests (`test_image_tools_subject_kind.py`); 520 affected backend tests green. Also fixed judge truncation: thinking models (gemini-flash) count reasoning against `max_tokens`, so `eval_image_quality` (400→2000) and `generate_images_for_packs.llm_image_judge` (500→2000) were silently unparseable/heuristic-passing before.

**Phase 2 — backfill (DONE).** New `backend/scripts/backfill_prod_images.py` reads rosters straight from PROD (fixes the on-disk-only gap: 209 fully-imageless + 291 partial packs, 450 URL drift), generates with the FIXED prompts, gates via the LLM concept judge, and IMMEDIATELY rehosts each image (never leaves an ephemeral FAL URL behind). Result: **1405/1443 targets imaged** (38 FAL refusals, 0 judge fails), ledger-estimate **$18.7 FAL** (conservative constants; true schnell-256px cost is far lower). End state verified in prod: **media_assets=3107 (all with bytes), 2994/2994 imaged characters on API-hosted URLs, 0 fal.media URLs left**; random samples serve `200 image/*` from the live API. Post-fix vision sample on the new images: see PR for the after-rate (report artifacts in the PR description).

**Phase 3 — structural fixes (PARTIAL).**
- `grimm-fairy-tale-archetype` (0 characters, broken): pack **retired** + `topics.current_pack_id` cleared in prod → `/quiz/start` falls through to the live agent (no dead end).
- Zero-question packs: prod recount found **24** (not 16). New `backend/scripts/backfill_baseline_questions.py` (gpt-4o-mini gen, gemini judge gate ≥75 w/ retry — the judge did real work: caught outcome-name leaks, near-dupes, off-theme scenarios, even cycling gear-shift factual errors). **20/24 packs passed** and are built+signed as `configs/precompute/starter_packs/zero_question_fix_2026-07-02.json` (+`.sig`, version 4 packs). **NOT YET IMPORTED** — the operator-endpoint POST was permission-blocked in this session. OWNER STEP (one command, from `backend/`):
  `curl -X POST "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io/api/v1/admin/precompute/import?force_upgrade=true" -H "Authorization: Bearer $OPERATOR_TOKEN" -H "X-Operator-2FA: <any-opaque-value>" -H "X-Archive-Signature: $(cat configs/precompute/starter_packs/zero_question_fix_2026-07-02.json.sig)" --data-binary @configs/precompute/starter_packs/zero_question_fix_2026-07-02.json`
  (or seed via `seed-prod-packs.yml` with `archive_path=` after merge). 4 stragglers kept failing the judge on genuine quality grounds → follow-up: `broadway-leading-role`, `dnd-character-class`, `k-pop-idols`, `cycling-pro-specialty`.
- <4-character packs (30): mostly **by design** (starter trios, versus-pairs: Powerpuff Girls=3, Pokémon starters=3, Death Note rivals=2, Stones-vs-Beatles=2) — no top-up needed; verified-good.

**Deferred to follow-ups (budget/time):** batch17 (75-candidate pool staged; generate→judge→image with fixed prompts→build/sign→seed) and aliases for top-traffic topics (`topic_aliases` is empty; lookup supports it; seed via archive `aliases` or direct inserts). Also: 38 FAL-refusal characters retryable via `backfill_prod_images.py --names-file`; the 121 pre-existing orphan `media_assets` rows (`storage_provider='fal'`, bulk-inserted 2026-07-02 15:17Z by another process) were left untouched.
>>>>>>> 0125d36 (docs(precompute): record Phases 1-3 execution + object-vs-person fix)
