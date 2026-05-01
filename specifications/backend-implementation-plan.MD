# Backend Implementation Plan — Pre-Computed Topic Knowledge Packs (§21)

> **Status:** Drafted after Iteration 2 of the §21 design reviews. This document is the
> step-by-step rollout sequence for the design captured in
> `specifications/backend-design.MD` §21.1–§21.19. Each phase lists the artefacts to
> build, the **tests that must be written first**, and the **gates that must be green
> before the phase is considered done** (per repo TDD policy).
>
> **Source of truth:** `backend-design.MD` §21 remains authoritative. If a gate below
> contradicts the spec, the spec wins and this plan must be amended.

---

## 0. Conventions and Universal Gates

These apply to **every** phase below. A phase is not "done" until all of these are
green in addition to its phase-specific gates.

- **Universal-G1 — Spec-first.** Any change to a phase's behaviour requires a prior
  edit to `backend-design.MD`; the AC referenced in that change must already exist or
  be added in the same PR.
- **Universal-G2 — Test-first.** Every new behaviour gets a failing test before any
  implementation code is written. Tests must fail for the right reason (the behaviour
  is missing — not because of a typo or import error).
- **Universal-G3 — Full pytest suite green** on `backend/.venv312` (Win local) AND on
  CI (Linux 3.11). Use `python -m pytest --tb=line -q --no-cov --maxfail=20`.
- **Universal-G4 — Quality gates green:**
  - `tests/security/test_quality_gates.py::test_app_passes_ruff`
  - `tests/security/test_quality_gates.py::test_app_passes_bandit_high_and_medium`
  - mypy clean over `backend/app/`
  - Ruff complexity cap (C901 ≤ 10) — no `# noqa: C901` introduced.
- **Universal-G5 — Backwards compatibility.** Every phase up to and including Phase 5
  MUST leave `precompute.enabled=false` as the default in `appconfig.local.yaml`. The
  existing `/quiz/start` byte-for-byte response is unchanged with the flag off.
- **Universal-G6 — Config additivity.** New config keys live under
  `quizzical.precompute.*` only; absent keys fall back to documented defaults
  (`AC-PRECOMP-LOOKUP-3`).
- **Universal-G7 — Forward-only schema additions.** This project does not use
  Alembic. Schema lives in two places that MUST stay in sync:
  1. **ORM models** in `backend/app/models/db.py` (source of truth for tests —
     `Base.metadata.create_all` runs against in-memory SQLite via
     `tests/fixtures/db_fixtures.py`).
  2. **Idempotent DDL** in `backend/db/init/init.sql` using
     `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and
     `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (the source of truth for the
     production Postgres init).
  Every phase that touches the schema MUST update both files in the same PR
  and include a test that asserts the ORM `Base.metadata.create_all` succeeds
  on SQLite (gives us the equivalent of an Alembic upgrade smoke).
- **Universal-G8 — README.** `backend/README.md` is updated in the same PR whenever
  routes, env vars, or operator workflows change.
- **Universal-G9 — No live network.** Tests use fakeredis, sqlite+pgvector stub or
  a Postgres test container, and httpx ASGI / `respx` mocks. No real FAL / LLM /
  CDN calls in CI.

**Rollback discipline.** Each phase is independently revertible. If a phase fails its
gates after merge, the rollback is either (a) revert the PR, or (b) flip the relevant
config flag (`precompute.enabled`, `precompute.lookup.enabled`,
`precompute.worker.enabled`, `precompute.image_storage.provider`). No phase introduces
a flag that cannot be turned off without redeploy.

---

## Phase 1 — Schema additions (forward-only, no read/write code)

**Goal.** Land the §21.3 domain model as new ORM models and matching idempotent
DDL in `db/init/init.sql`. No application code reads or writes them yet.

### Build
- New ORM models in `backend/app/models/db.py` (or split into `db_precompute.py`
  imported from `db.py`): `Topic`, `TopicAlias`, `TopicPack`, `Synopsis`,
  `CharacterSet`, `BaselineQuestionSet`, `Question`, `MediaAsset`, `ContentFlag`,
  `PrecomputeJob`, `EvaluatorTrainingExample`, `EmbeddingsCache`, `AuditLog`.
- Additive columns on existing `Character` ORM model: `canonical_key Text`,
  `embedding Vector(384)`, `evaluator_score SmallInteger`,
  `flag_count Integer DEFAULT 0`, `image_asset_id UUID NULL`,
  `policy_status Text DEFAULT 'allowed'`.
- Idempotent DDL appended to `backend/db/init/init.sql`:
  - All new `CREATE TABLE IF NOT EXISTS` blocks with the same columns/types.
  - `ALTER TABLE characters ADD COLUMN IF NOT EXISTS ...` for each new column.
  - All indexes from §21.3 (slug unique, `ivfflat (topics.embedding)`, etc.)
    in `DO $$ ... IF NOT EXISTS ...` blocks matching the existing style.
  - `CREATE EXTENSION IF NOT EXISTS unaccent;` near the top of the
    extensions block.
- Backfill SQL appended to `init.sql`: `UPDATE characters SET canonical_key =
  lower(name) WHERE canonical_key IS NULL;` with a follow-up disambiguation
  pass for collisions.

### Tests written first (must fail before implementation)
- `backend/tests/unit/db/test_precomp_schema.py`
  - `test_orm_metadata_creates_all_tables` — builds an in-memory SQLite via
    the existing `sqlite_db_session` fixture; asserts every new table from
    §21.3 exists and exposes the documented columns.
  - `test_topics_slug_unique_constraint` — second insert with same slug raises
    `IntegrityError`.
  - `test_topic_packs_unique_topic_version` — `(topic_id, version)` enforced.
  - `test_synopses_content_hash_unique` — second insert with same hash raises.
  - `test_topic_aliases_pk_alias_normalized_topic_id` — PK enforced.
  - `test_characters_has_new_precomp_columns` — ORM mapper exposes the new
    columns with the documented defaults / nullability.
  - `test_characters_canonical_key_default_lowercased` — inserting a Character
    with `name='Foo Bar'` and no explicit `canonical_key` lands
    `canonical_key='foo bar'` after the application-level helper runs (the
    ORM mapper applies the default; SQLite `unaccent` is unavailable so the
    application normaliser is what we test against).
  - `test_audit_log_append_only_at_app_layer` — the ORM model exposes no
    `update()` helper and the documented dao function refuses updates (the
    DB-level grant is Postgres-only and verified separately by
    `db/init/init.sql` review, not by SQLite tests).
- `backend/tests/unit/db/test_init_sql_parity.py`
  - `test_init_sql_creates_all_new_tables_in_postgres_or_falls_back` — if a
    real `TEST_PG_DSN` is provided in env, runs `init.sql` against it and
    asserts the same tables exist; otherwise SKIPs with a documented reason
    (so CI without Postgres still passes).

### Phase-1 gates
- Universal G1–G9.
- `Base.metadata.create_all` succeeds against the test SQLite engine (covered by
  the new schema tests using the existing `sqlite_db_session` fixture).
- `init.sql` is re-runnable without errors (idempotent guard checked by manual
  `psql -f` on a developer Postgres OR the optional `test_init_sql_parity`
  Postgres-backed test when a DSN is provided).
- Existing test suite (973 tests pre-§21) is **byte-for-byte unchanged** — no
  test imports any new symbol.
- `pytest backend/tests/unit/db/test_precomp_schema.py` passes.
- Bandit + ruff still green; no new dependency added.

---

## Phase 2 — Read-path shim (off by default)

**Goal.** Add the lookup logic in `app/services/precompute/lookup.py` and wire it into
`/quiz/start` behind `precompute.enabled=False`. With the flag off, behaviour is
identical to today; with the flag on **and zero published packs**, behaviour is also
identical (every lookup returns MISS → live agent).

### Build
- `app/services/precompute/lookup.py`:
  - `async def resolve_topic(db, redis, raw_text) -> TopicResolution | None` —
    alias-exact → slug-exact → vector NN at `τ_match`.
  - `async def fetch_pack(db, redis, topic_id) -> ResolvedPack | None` — reads
    Redis `tk:pack:{topic_id}` first; on miss, SELECTs from
    `mv_topic_pack_resolved` (created in Phase 1's later migration if not present;
    otherwise plain JOIN, see Phase 4 note).
  - Defensive double-check that `topic_packs.status='published'` even if
    `topics.current_pack_id` points at a quarantined row (`AC-PRECOMP-LOOKUP-4`).
- `app/api/dependencies.py`: new dep `get_precompute_lookup` injecting the service.
- `app/api/v1/quiz.py::start_quiz`: branch on `settings.precompute.enabled`. On HIT,
  build the existing `StartQuizResponse` from the resolved pack (no agent call). On
  MISS or flag off, current path runs unchanged.
- `appconfig.local.yaml`: add `quizzical.precompute.enabled: false` and
  `quizzical.precompute.thresholds: {match: 0.86, pass: 7, strong_trigger: 5}`.

### Tests written first
- `tests/unit/services/precompute/test_lookup.py`
  - `test_alias_exact_match_returns_pack` (`AC-PRECOMP-LOOKUP-1`)
  - `test_slug_exact_match_returns_pack`
  - `test_vector_nn_above_threshold_returns_pack`
  - `test_vector_nn_below_threshold_returns_none` (`AC-PRECOMP-LOOKUP-2`)
  - `test_thresholds_read_from_config_with_defaults` (`AC-PRECOMP-LOOKUP-3`)
  - `test_quarantined_pack_is_never_returned` (`AC-PRECOMP-LOOKUP-4`)
  - `test_no_model_call_on_read_path` — patches every LLM/embedding client to
    raise on call; lookup must complete using the precomputed `topics.embedding`
    only (`AC-PRECOMP-PERF-5`).
- `tests/integration/api/test_quiz_start_precompute_off.py`
  - `test_start_quiz_byte_for_byte_unchanged_with_flag_off` — flag false,
    assert response shape and headers byte-equivalent to baseline snapshot.
- `tests/integration/api/test_quiz_start_precompute_miss.py`
  - `test_start_quiz_miss_falls_through_to_agent` — flag on but no published pack
    exists; asserts the agent path runs.
- `tests/security/test_precompute_auth.py`
  - `test_pack_hit_still_enforces_turnstile_and_rate_limit` (`AC-PRECOMP-LOOKUP-5`)
- `tests/unit/services/precompute/test_lookup_observability.py`
  - `test_lookup_emits_structured_log` (`AC-PRECOMP-OBS-1`)

### Phase-2 gates
- Universal G1–G9.
- Flag is `false` by default. CI runs the existing suite with no behaviour drift.
- New tests pass on a Postgres service container with pgvector.
- Lookup p95 ≤ 25 ms (DB-only) on the test bench (`AC-PRECOMP-LOOKUP-1`); a perf
  micro-test in `tests/performance/test_precomp_lookup_perf.py` enforces it with a
  100-iteration warmup-then-measure pattern.

---

## Phase 3 — Build worker + admin endpoints (operator-gated)

**Goal.** Add the write-path pipeline (§21.5) and operator endpoints (§21.12) behind
`OPERATOR_TOKEN`. Worker can run, but no automated promotion to prod.

### Build
- `app/services/precompute/builder.py` — generation pipeline with tier escalation
  (cheap → strong → strong+search), `max_build_attempts=3`, evaluator wired in.
- `app/services/precompute/evaluator.py` — structured output
  `{score, blocking_reasons[], non_blocking_notes[]}` (`AC-PRECOMP-QUAL-5`); two-judge
  consensus for factual artefacts (`AC-PRECOMP-QUAL-2`); Tier-3 source citations
  (`AC-PRECOMP-QUAL-6`); cross-pack consistency check (`AC-PRECOMP-QUAL-7`).
- `app/services/precompute/dedup.py` — canonical-key/content-hash dedup helpers
  (`AC-PRECOMP-DEDUP-1..3`).
- `app/services/precompute/cost_guard.py` — pre-attempt daily-budget check
  (`AC-PRECOMP-BUILD-5`); Tier-3 cutoff at 75% of budget (`AC-PRECOMP-COST-6`).
- `app/services/precompute/jobs.py` — `precompute_jobs` state machine
  (`AC-PRECOMP-BUILD-1`); single transactional persistence
  (`AC-PRECOMP-BUILD-4`).
- `app/api/v1/admin/precompute.py` — `POST /admin/precompute/enqueue`,
  `GET /admin/precompute/jobs`, `POST /admin/precompute/promote/{pack_id}`,
  `POST /admin/precompute/rollback/{topic_id}`, `GET /admin/precompute/cost`,
  `POST /admin/users/forget`. All require `OPERATOR_TOKEN`; production promote
  additionally requires `X-Operator-2FA` (`AC-PRECOMP-SEC-4`).
- `app/api/v1/healthz.py::precompute` — operator-gated health view
  (`AC-PRECOMP-OBJ-3`).
- `app/services/precompute/audit.py` — append to `audit_log` for every operator
  action (`AC-PRECOMP-FLAG-6`).
- `app/services/precompute/safety.py` — topic policy gate (§21.9):
  - `banned` topics cannot be enqueued; worker returns ledger error
    (`AC-PRECOMP-SAFETY-1`).
  - `restricted` topics force evaluator tier `strong+search` and `τ_pass=9`
    (`AC-PRECOMP-SAFETY-2`).
  - User-supplied category text is wrapped in a delimited `<user_input>` block
    and never templated into a system prompt (`AC-PRECOMP-SAFETY-3`).
  - Vision-evaluator rejections are recorded with reason code (NSFW, minor,
    logo, off-topic) for offline review (`AC-PRECOMP-SAFETY-4`).

### Tests written first
- `tests/unit/services/precompute/test_builder_state_machine.py`
  - `test_status_transitions_are_legal` (`AC-PRECOMP-BUILD-1`)
  - `test_failed_build_persists_zero_rows` (`AC-PRECOMP-BUILD-2`)
  - `test_tier_escalation_order_recorded` (`AC-PRECOMP-BUILD-3`)
  - `test_partial_failure_rolls_back_atomically` (`AC-PRECOMP-BUILD-4`)
- `tests/unit/services/precompute/test_cost_guard.py`
  - `test_pre_attempt_budget_check_skips_when_overspent` (`AC-PRECOMP-BUILD-5`)
  - `test_tier3_suppressed_at_75pct_budget` (`AC-PRECOMP-COST-6`)
- `tests/unit/services/precompute/test_dedup.py`
  - `test_existing_canonical_key_is_reused` (`AC-PRECOMP-DEDUP-1`)
  - `test_existing_content_hash_is_reused` (`AC-PRECOMP-DEDUP-2`)
  - `test_existing_prompt_hash_skips_fal_call` (`AC-PRECOMP-DEDUP-3`)
- `tests/unit/services/precompute/test_evaluator.py`
  - `test_structured_output_required` (`AC-PRECOMP-QUAL-5`)
  - `test_two_judge_consensus_min_score` (`AC-PRECOMP-QUAL-2`)
  - `test_two_judge_divergence_triggers_tier3` (`AC-PRECOMP-QUAL-2`)
  - `test_tier3_requires_source_citations` (`AC-PRECOMP-QUAL-6`)
  - `test_cross_pack_consistency_rejects_drifted_profile` (`AC-PRECOMP-QUAL-7`)
- `tests/security/test_admin_precompute_auth.py`
  - `test_endpoints_require_operator_token` (401 without)
  - `test_prod_promote_requires_2fa_header` (`AC-PRECOMP-SEC-4`)
  - `test_ci_environment_cannot_promote_to_prod` (`AC-PRECOMP-PROMOTE-3`)
- `tests/integration/api/test_admin_precompute_endpoints.py`
  - happy-path enqueue → run → promote-staging → fetch
  - `test_concurrent_publish_is_atomic` (`AC-PRECOMP-PROMOTE-1`)
  - `test_publish_invalidates_redis_cache_before_commit_release`
    (`AC-PRECOMP-PROMOTE-2`)
  - `test_rollback_restores_prior_pack_atomically` (`AC-PRECOMP-PROMOTE-4`)
- `tests/security/test_precompute_outbound_ssrf.py`
  - `test_outbound_client_rejects_loopback` (`AC-PRECOMP-SEC-1`)
  - `test_outbound_client_rejects_private_cidrs` (`AC-PRECOMP-SEC-1`)
  - `test_dns_pinning_resists_rebind` (`AC-PRECOMP-SEC-1`)
- `tests/security/test_evaluator_prompt_injection.py`
  - `test_retrieved_block_does_not_let_model_break_out` (`AC-PRECOMP-SEC-2`)
- `tests/security/test_secret_strength.py`
  - `test_prod_startup_fails_closed_without_secrets` (`AC-PRECOMP-SEC-9`)
- `tests/unit/services/precompute/test_audit_log.py`
  - `test_operator_action_writes_audit_row` (`AC-PRECOMP-FLAG-6`)
- `tests/unit/services/precompute/test_observability.py`
  - `test_build_emits_structured_log` (`AC-PRECOMP-OBS-2`)
  - `test_daily_spend_gauge_exposed` (`AC-PRECOMP-OBS-3`)
- `tests/security/test_precompute_safety.py`
  - `test_banned_topic_cannot_be_enqueued` (`AC-PRECOMP-SAFETY-1`)
  - `test_restricted_topic_forces_tier3_and_pass_threshold_9`
    (`AC-PRECOMP-SAFETY-2`)
  - `test_user_category_never_enters_system_prompt` — asserts injection markers
    like `</system>` survive verbatim inside the `<user_input>` block and never
    appear in the rendered system message (`AC-PRECOMP-SAFETY-3`).
  - `test_vision_evaluator_rejection_records_reason_code`
    (`AC-PRECOMP-SAFETY-4`)

### Phase-3 gates
- Universal G1–G9.
- All new tests pass (~35 new tests minimum).
- `precompute.enabled` still defaults to `false`; admin endpoints reachable only
  with `OPERATOR_TOKEN`.
- Bandit clean; no new bare `except Exception` except where helper docstring
  documents fail-safe behaviour.
- Worker is a callable function — **no background scheduler started yet**.

---

## Phase 4 — Performance: materialised view + Redis pack cache

**Goal.** Land §21.18 Review 8: hot-path SELECT becomes one MV row + array unnest;
Redis pack cache absorbs the JOIN cost.

### Build
- `precomp_0002_mv_topic_pack_resolved` — Postgres materialised view appended
  to `db/init/init.sql` as `CREATE MATERIALIZED VIEW IF NOT EXISTS
  mv_topic_pack_resolved AS ...` plus a `REFRESH MATERIALIZED VIEW CONCURRENTLY`
  helper SQL invoked from `publish()`. SQLite tests skip the MV path and use a
  plain JOIN under the hood (the resolver abstracts over both).
- `app/services/precompute/cache.py`:
  - `tk:pack:{topic_id}` SETNX fill lock; TTL 1 h; transactional invalidation.
  - Hot-character pinning at `media:hot:{asset_id}` for refs > 200
    (`AC-PRECOMP-PERF-6`).
- `app/api/v1/quiz.py`: emit `103 Early Hints` Link headers preloading expected
  media URIs on HIT (`AC-PRECOMP-PERF-3`).

### Tests written first
- `tests/unit/services/precompute/test_mv_topic_pack_resolved.py`
  - `test_publish_refreshes_mv_concurrently` (`AC-PRECOMP-PERF-1`)
- `tests/unit/services/precompute/test_pack_cache.py`
  - `test_setnx_fill_lock_prevents_thundering_herd` (`AC-PRECOMP-PERF-2`) —
    100 concurrent fakeredis fillers result in ≤ 1 DB hit.
  - `test_cache_invalidated_transactionally_on_publish`
  - `test_cache_invalidated_transactionally_on_quarantine`
- `tests/unit/services/precompute/test_hot_character_pinning.py`
  - `test_character_pinned_when_ref_count_above_200` (`AC-PRECOMP-PERF-6`)
- `tests/integration/api/test_quiz_start_early_hints.py`
  - `test_hit_path_emits_103_early_hints` (`AC-PRECOMP-PERF-3`)
- `tests/performance/test_precomp_hot_path_perf.py`
  - `test_pack_lookup_p95_under_25ms` (`AC-PRECOMP-LOOKUP-1`) — re-run with
    MV+cache enabled; budget tightens vs Phase 2.

### Phase-4 gates
- Universal G1–G9.
- Concurrent-fill test demonstrably collapses to ≤ 1 DB read.
- p95 ≤ 25 ms DB-only on the bench.
- No regression in Phase 1–3 tests.

---

## Phase 5 — Image storage: Phase 0 → 0.5 (local rehost)

**Goal.** Implement `media_resolver` abstraction with two providers initially:
`fal` (current FAL CDN URL passthrough) and `local` (BYTEA in DB, served via
`GET /api/v1/media/{asset_id}`). No Azure yet.

### Build
- `app/services/media/resolver.py` — provider switch driven by
  `precompute.image_storage.provider ∈ {fal, local}`.
- `app/services/media/local_provider.py` — store bytes in `media_assets.bytes_blob`,
  return `/api/v1/media/{asset_id}` URL.
- `app/api/v1/media.py::get_media_asset` — content-addressed; returns
  `Cache-Control: public, max-age=31536000, immutable` and a strong ETag derived
  from `content_hash` (`AC-PRECOMP-PERF-4`, `AC-PRECOMP-IMG-3`).
- Background rehost trigger: when a `media_assets` row's `expires_at ≤ 7 days`,
  the next worker run rehosts via the active provider (`AC-PRECOMP-IMG-2`).

### Tests written first
- `tests/unit/services/media/test_resolver.py`
  - `test_storage_uri_renders_identically_across_providers`
    (`AC-PRECOMP-IMG-1`)
- `tests/integration/api/test_media_endpoint.py`
  - `test_immutable_cache_headers_present` (`AC-PRECOMP-IMG-3`,
    `AC-PRECOMP-PERF-4`)
  - `test_etag_is_content_hash`
  - `test_if_none_match_returns_304`
- `tests/unit/services/media/test_rehost_trigger.py`
  - `test_expiring_asset_is_queued_for_rehost` (`AC-PRECOMP-IMG-2`)

### Phase-5 gates
- Universal G1–G9.
- `provider=fal` path is byte-equivalent to today.
- `provider=local` path serves images with the documented headers.
- No Azure SDK in `pyproject.toml` yet.

---

## Phase 6 — Content flagging + auto-quarantine

**Goal.** Implement §21.8 self-healing: `POST /content/flag`, ip-hashed dedup,
quarantine threshold, cascade quarantine on asset/character degradation.

### Build
- `app/api/v1/content.py::flag_content` — validates `reason_code` allowlist,
  caps `reason_text` length, PII-scrubs, HMAC-hashes IP
  (`AC-PRECOMP-FLAG-1..3`).
- `app/services/precompute/flag_aggregator.py` — distinct-IP threshold within
  window → atomic quarantine in same transaction (`AC-PRECOMP-FLAG-4`).
- Cascade: when `characters.evaluator_score` or `media_assets.evaluator_score`
  drops below `τ_pass`, every pack referencing it is auto-quarantined
  (`AC-PRECOMP-SEC-6`).
- Honeypot reason codes silently dropped; > 50 distinct-target flags/day per
  ip_hash → shadow-discard (`AC-PRECOMP-SEC-7`).

### Tests written first
- `tests/unit/api/test_flag_content_validation.py`
  - `test_unknown_reason_code_returns_422` (`AC-PRECOMP-FLAG-1`)
  - `test_reason_text_length_capped` (`AC-PRECOMP-FLAG-2`)
  - `test_raw_ip_never_persisted` (`AC-PRECOMP-FLAG-3`)
- `tests/integration/api/test_flag_quarantine_threshold.py`
  - `test_distinct_ips_above_threshold_quarantines_pack` (`AC-PRECOMP-FLAG-4`)
  - `test_quarantined_pack_routes_to_live_agent` (`AC-PRECOMP-FLAG-5`)
- `tests/unit/services/precompute/test_cascade_quarantine.py`
  - `test_lowering_character_score_quarantines_referencing_packs`
    (`AC-PRECOMP-SEC-6`)
- `tests/security/test_flag_honeypot.py`
  - `test_honeypot_code_silently_dropped` (`AC-PRECOMP-SEC-7`)
  - `test_abusive_ip_hash_shadow_discarded` (`AC-PRECOMP-SEC-7`)
- `tests/security/test_topics_suggest_hardening.py`
  - `test_query_below_2_chars_rejected` (`AC-PRECOMP-SEC-3`)
  - `test_max_8_results` (`AC-PRECOMP-SEC-3`)
  - `test_60_per_minute_rate_limit` (`AC-PRECOMP-SEC-3`)
- `tests/unit/api/test_admin_users_forget.py`
  - `test_forget_zeroes_flag_linkage` (`AC-PRECOMP-SEC-8`)

### Phase-6 gates
- Universal G1–G9.
- Quarantine path unit-tested end-to-end with fakeredis + in-memory PG.
- No raw IPs anywhere in the test DB after the flagging suite runs.

---

## Phase 7 — Cost controls + observability + dedup

**Goal.** §21.18 Review 11 in full: embeddings cache, batched evaluator/generator
calls, off-peak scheduling, cost view, opt-in per-question images, opportunistic
live-session enqueue.

### Build
- `app/services/embeddings/cache.py` — `embeddings_cache` lookup by `text_hash`
  (`AC-PRECOMP-COST-1`).
- Batched evaluator entry point (`AC-PRECOMP-COST-2`); single-call generator for
  baseline question sets (`AC-PRECOMP-COST-3`).
- `precompute_jobs.cost_cents` populated; SQL view `v_topic_cost_30d`;
  `GET /admin/precompute/cost` (`AC-PRECOMP-COST-4`).
- Worker concurrency split (`daytime_concurrency`, `offpeak_concurrency`,
  `offpeak_window_utc`) (`AC-PRECOMP-COST-5`).
- Per-question image opt-in (default off) (`AC-PRECOMP-COST-7`).
- Live-session enqueue gate: `current_pack_id IS NULL AND popularity_rank < 5000`
  (`AC-PRECOMP-COST-8`).

### Tests written first
- `tests/unit/services/embeddings/test_cache.py`
  - `test_repeated_text_hits_cache` (`AC-PRECOMP-COST-1`)
- `tests/unit/services/precompute/test_batched_calls.py`
  - `test_evaluator_one_call_for_all_artefacts` (`AC-PRECOMP-COST-2`)
  - `test_generator_one_call_for_baseline_set` (`AC-PRECOMP-COST-3`)
- `tests/unit/services/precompute/test_cost_view.py`
  - `test_v_topic_cost_30d_aggregates_correctly` (`AC-PRECOMP-COST-4`)
- `tests/unit/services/precompute/test_offpeak_scheduling.py`
  - `test_concurrency_switches_inside_window` (`AC-PRECOMP-COST-5`)
- `tests/unit/services/precompute/test_per_question_images_opt_in.py`
  - `test_default_off_no_image_calls_made` (`AC-PRECOMP-COST-7`)
- `tests/integration/api/test_live_session_enqueue.py`
  - `test_enqueue_skipped_for_popular_topic` (`AC-PRECOMP-COST-8`)

### Phase-7 gates
- Universal G1–G9.
- Cost-attribution query returns sane numbers on a synthetic dataset.
- Off-peak window test uses `freezegun` (or equivalent) — no real `time.sleep`.

---

## Phase 8 — Quality: versioned prompts, golden set, drift detection

**Goal.** §21.18 Review 9 in full.

### Build
- `app/services/precompute/prompts.py` — every prompt template carries
  `(semver, sha256)`; both written into `model_provenance` and
  `precompute_jobs.evaluator_history` (`AC-PRECOMP-QUAL-1`).
- `Analysis/golden/precompute_*.jsonl` — ~200 hand-graded items per artefact kind
  (synopsis / character / question). Promotion harness rejects evaluator changes
  that don't strictly improve precision AND recall (`AC-PRECOMP-QUAL-3`).
- `app/jobs/evaluator_drift_probe.py` — weekly task: re-evaluate 5% sample;
  pass-rate drop > 10pp → set `evaluator_drift_paused`, alert
  (`AC-PRECOMP-QUAL-4`).

### Tests written first
- `tests/unit/services/precompute/test_prompt_versioning.py`
  - `test_prompt_version_and_hash_in_provenance` (`AC-PRECOMP-QUAL-1`)
  - `test_rerun_with_new_version_creates_new_attempt`
- `tests/unit/services/precompute/test_golden_promotion.py`
  - `test_no_promote_without_strict_pr_improvement` (`AC-PRECOMP-QUAL-3`)
- `tests/unit/services/precompute/test_drift_probe.py`
  - `test_pass_rate_drop_pauses_evaluator` (`AC-PRECOMP-QUAL-4`)

### Phase-8 gates
- Universal G1–G9.
- Golden set committed (or stub committed with TODO if real grading is pending —
  but the harness must run against it).
- Drift probe is invocable via CLI for operator verification.

---

## Phase 9 — Starter catalog + first dev seed (~50 topics)

**Goal.** Bundle the starter catalog (§21.18 Review 7 #2) and dry-run the worker
in dev. **Production still untouched.**

### Build
- `configs/precompute/starter_packs/*.json` — versioned, hash-stamped JSON.
- `scripts/import_packs.py` — verifies detached signature
  (`AC-PRECOMP-SEC-5`); idempotent on `content_hash` / `composition_hash`
  (`AC-PRECOMP-MIGR-6`); imported on first deploy when DB has zero published
  packs (`AC-PRECOMP-OBJ-2`).

### Tests written first
- `tests/unit/scripts/test_import_packs.py`
  - `test_unsigned_archive_refused` (`AC-PRECOMP-SEC-5`)
  - `test_import_idempotent_on_content_hash` (`AC-PRECOMP-MIGR-6`)
  - `test_import_skipped_when_packs_already_present` (`AC-PRECOMP-OBJ-2`)
- `tests/integration/scripts/test_starter_seed_dev.py`
  - end-to-end dry-run on a Postgres test container with `precompute.enabled=true`,
    asserts each starter topic resolves to a HIT.

### Phase-9 gates
- Universal G1–G9.
- Starter import works on a clean DB and is a no-op on a populated one.
- `OPERATOR_TOKEN` and `FLAG_HMAC_SECRET` documented in `backend/README.md`
  with rotation guidance (`AC-PRECOMP-SEC-9`).

---

## Phase 10 — Staging enable + telemetry-driven enqueue

**Goal.** Flip `precompute.enabled=true` in **staging only**. Observe.

### Build
- No new code beyond config flip and dashboards.
- `/healthz/precompute` watched: `hit_rate_24h`, `top_misses_24h`
  (`AC-PRECOMP-OBJ-3`).
- Per-IP-hash recent-topics cache active (`AC-PRECOMP-OBJ-4`).
- Two-phase `/quiz/start` response shape: `pack_status ∈ {hit, miss, degraded}`
  + placeholder synopsis on miss (`AC-PRECOMP-OBJ-1`).

### Tests written first
- `tests/integration/api/test_quiz_start_two_phase_response.py`
  - `test_hit_response_shape` (`AC-PRECOMP-OBJ-1`)
  - `test_miss_response_shape_first_paint_identical` (`AC-PRECOMP-OBJ-1`)
- `tests/unit/services/precompute/test_recent_topics_cache.py`
  - `test_recent_cache_does_not_affect_rate_limit` (`AC-PRECOMP-OBJ-4`)

### Phase-10 gates
- Universal G1–G9.
- 7-day staging soak with: 0 spec-related 5xx, p95 within budget
  (`AC-PRECOMP-LOOKUP-1`), no quarantine cascades unaccounted for.
- Operator dashboard surfaces `hit_rate_24h` and top misses.

---

## Phase 11 — Production promote (manual, small subset)

**Goal.** Operator-driven promotion of a curated subset (~10–20 packs) to prod.
`precompute.enabled=true` in prod. All other topics still live-agent.

### Build
- No code changes. This is operator workflow only.
- Promote requires `OPERATOR_TOKEN` + `X-Operator-2FA` (`AC-PRECOMP-SEC-4`).
- Smoke run after each promote.

### Phase-11 gates
- Universal G1–G9.
- Live smoke + Trivy + image scan green.
- Rollback drill executed and documented: time-to-rollback ≤ 5 minutes.
- Operator runbook in `backend/README.md` includes: promote, rollback, quarantine,
  forget, drift-probe, cost-check.

---

## Phase 12 — Image storage Phase 1 (Azure Blob)

**Goal.** §21.18 Review 12 + `AC-PRECOMP-MIGR-1..9`. Add `azure` provider to the
media resolver. Dual-write window. Then cutover.

### Build
- `app/services/media/azure_provider.py` — content-addressed
  `{hash[0:2]}/{hash[2:4]}/{hash}.{ext}` (`AC-PRECOMP-MIGR-4`); container is
  public-read with `Cache-Control: public, max-age=31536000, immutable` set as a
  blob property (`AC-PRECOMP-MIGR-5`).
- Dual-write window: `provider=local+azure` for 7 days
  (`AC-PRECOMP-MIGR-1`).
- One-shot migrator script: replays all `local` rows to azure, verifies
  `content_hash` byte-for-byte (`AC-PRECOMP-MIGR-3`), then drops `bytes_blob`
  column (`AC-PRECOMP-MIGR-2`).
- DR: bytes lost → re-derive from `media_assets.prompt_payload`; vision evaluator
  must re-pass before reuse (`AC-PRECOMP-MIGR-7`).
- Cost telemetry: blob GB + CDN egress GB surfaced
  (`AC-PRECOMP-MIGR-8`).
- `storage_provider` visible in operator UIs (`AC-PRECOMP-MIGR-9`).

### Tests written first
- `tests/unit/services/media/test_azure_provider.py`
  - `test_path_structure_two_deep_prefix` (`AC-PRECOMP-MIGR-4`)
  - `test_blob_property_immutable_cache` (`AC-PRECOMP-MIGR-5`)
- `tests/integration/migration/test_dual_write_resolver.py`
  - `test_resolver_prefers_blob_falls_back_to_local` (`AC-PRECOMP-MIGR-1`)
- `tests/unit/scripts/test_local_to_azure_migrator.py`
  - `test_content_hash_mismatch_aborts_flip` (`AC-PRECOMP-MIGR-3`)
  - `test_drop_column_only_after_full_success` (`AC-PRECOMP-MIGR-2`)
- `tests/unit/services/media/test_dr_rederive.py`
  - `test_rederived_bytes_revisit_vision_evaluator` (`AC-PRECOMP-MIGR-7`)
- `tests/unit/services/precompute/test_storage_cost_telemetry.py`
  - `test_blob_gb_and_cdn_egress_in_cost_view` (`AC-PRECOMP-MIGR-8`)
- `tests/integration/api/test_admin_jobs_storage_provider.py`
  - `test_storage_provider_visible_in_admin_jobs_response`
    (`AC-PRECOMP-MIGR-9`)

### Phase-12 gates
- Universal G1–G9.
- Migration is reversible (resolver can be flipped back to `local` until column
  drop).
- No SAS rotation logic introduced (container is public-read by design).
- README + runbook updated with Azure resource list, container name, and
  rotation-not-required note.

---

## Phase 13 — CDN + final image perf

**Goal.** Front the public-read container with Azure Front Door / Cloudflare;
verify cache headers honoured edge-to-origin.

### Build
- DNS / CDN config (infra repo).
- Backend change limited to: log CDN egress for cost view; emit synthetic probe
  every 5 min.

### Phase-13 gates
- Universal G1–G9.
- Synthetic probe shows ≥ 95% cache-hit at edge after 24 h warmup.
- Cost telemetry for CDN egress matches CDN-vendor billing within ±5%.

---

## Cross-Cutting Test Matrix (must stay green at every phase)

| Category | Pre-§21 baseline | Add per phase |
|---|---|---|
| Unit (`tests/unit/`) | 970+ | +5–25 per phase |
| Integration (`tests/integration/`) | existing | +2–6 per phase |
| Security (`tests/security/`) | existing | +3–6 per phase |
| Performance (`tests/performance/`) | existing | Phase 2, 4 only |
| Reliability (`tests/reliability/`) | existing | Phase 6 (cascade) |
| Smoke (`tests/smoke/`) | existing | Phase 11 (operator runbook) |
| Quality gates | ruff, bandit -ll, mypy | Unchanged behaviour, must stay 0 findings |

**Regression discipline.** No phase may push the previous phase's gate from green
to yellow. If it does, the phase rolls back before merge.

---

## Implementation Sequencing Notes

- Phases **1, 2, 5** are independently shippable to prod with `precompute.enabled=
  false`. They land schema, lookup shim, and the media abstraction without affecting
  user-facing behaviour.
- Phases **3, 4, 6, 7, 8** add capability **but do not turn on user-visible behaviour
  changes**. They can land in any order within themselves; suggested order matches
  cost-of-rebase.
- Phases **9, 10, 11** are sequential — staging soak gates production.
- Phases **12, 13** are sequenced after Phase 11, but Phase 12 can begin its
  *implementation* in parallel with Phase 10's soak. The cutover (`drop bytes_blob`)
  must wait until after Phase 11 is stable.

## Out of Scope for v1 (parking lot)

- Per-user adaptive question pre-computation (still live).
- Final-result profile pre-rendering (still live).
- Multi-region blob replication (single-region until prod scale demands it).
- Operator UI for content review (CLI + audit_log only in v1).
- Real-time evaluator drift dashboards (weekly probe is sufficient at this scale).

---

*End of implementation plan. Re-check against `backend-design.MD` §21 on every
amendment; the spec is the source of truth.*
