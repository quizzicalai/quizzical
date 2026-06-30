# Quizzical / Quafel — Failure + Security Audit & Hardening

**Date:** 2026-06-30
**Scope:** Deep failure-mode + security audit of the FastAPI + LangGraph backend, the FE error contract, the cost/abuse surface, and the image (FAL) + Turnstile boundaries. Run on top of the `feat/perf-trio` branch (the just-merged perf work), after the durable-jobs hardening (PR #18) and the whimsical-error-code system landed.
**Method:** A failure-and-security pass with a disprove-first mandate, tracing every cost guard, every error-rendering path, and every trust boundary (XFF/remoteip, FAL URL allowlist, request body parsing) to the code as it stands. Each candidate finding was confirmed against the source before being accepted.

> Severity: **CRIT/HIGH** = exploitable / unbounded cost / data loss. **MED** = a real correctness or cost hole that bites under real or adversarial traffic. **LOW** = precision / consistency / privacy polish with a concrete (if small) blast radius.

---

## Executive summary & posture

**Posture is STRONG. There are zero critical and zero high-severity findings.** The cost gate is unbreakable from the input side (Turnstile is Redis-independent and fail-closed; the per-IP, per-session, and cluster-wide caps stack); there is no injection, no SSRF, and no PII leak on the paths reviewed; the whimsical-error system and the durable-jobs / crash-recovery subsystem are robust. This audit is therefore not a "stop the launch" report — it is a punch list of the **two confirmed mediums** (both cost/reliability, both exploitable only as *overshoot*, not breach) and the **worthwhile lows** (precision, consistency, privacy, and one config footgun).

The recurring shape of the findings is **"a guard that degrades to OFF rather than to SAFE."** The daily $ ceiling was a soft (read-only) ceiling that a concurrent burst could overshoot; it failed fully open during a Redis outage; the FAL URL allowlist silently disabled itself when emptied; a corrupt result blob produced a *permanent* 500 instead of a recoverable terminal state. The remediation closes each of those so the degrade direction is SAFE (near-hard ceiling, coarse local cap, deny-external default, fatal-fast 422) **while preserving the fail-OPEN philosophy everywhere a Redis blip must not become a user-facing DoS.**

All 10 items below are implemented on `feat/security-hardening` (stacked on `feat/perf-trio`), each as its own commit with tests; the full backend suite is green per-dir and ruff is clean.

### Posture at a glance

| Severity | Count | Status |
|---|---:|---|
| **CRIT** | 0 | — |
| **HIGH** | 0 | — |
| **MED** | 2 | fixed (#1, #2) |
| **LOW** | 8 | fixed (#3–#10) |

---

## Findings & fixes

### HIGH-VALUE (cost + reliability)

**#1 — [MED, exploitable as overshoot] Budget reservation at admission.**
`_enforce_global_daily_cost_ceiling` only READ the daily cents counter; per-call spend is recorded *after* the agent runs. Between an admission and the first recorded cent, a concurrent burst all reads the same pre-burst total and is admitted en masse, overshooting `daily_budget_usd` by `(in-flight count) × (per-quiz cost)` — a *soft*, not hard, ceiling.
**Fix:** `/quiz/start` now RESERVES an estimated per-quiz cost via an atomic `INCRBY` at admission (`cost_meter.reserve_estimated_cents`) and re-checks the ceiling against the reserved total, so concurrent admissions see each other (soft → near-hard). The reservation is RECONCILED/released when the inline paid run finishes (`cost_meter.reconcile_reservation`, `actual=0`) — the per-call meter has accrued the real spend by then, so releasing avoids double-counting and the counter converges to true spend. The precompute short-circuit and every error path release it too. **Fail-OPEN preserved:** a reservation `INCRBY` fault returns `None` and the request proceeds with nothing reserved, exactly as the read-only breaker did. New config `security.live_cost_guard.reservation_estimate_usd`.

**#2 — [MED] Corrupt `final_result` produced a permanent 500.**
`get_quiz_status` validated `final_result` FIRST and on `FinalResult.model_validate` failure re-raised `QF-MALFORMED-RESULT` (500) on **every** poll, forever: the cache miss-path rehydrates the SAME bad blob from Postgres, and the recovery sweeper sees a truthy `final_result` → marks the job succeeded → never repairs it. A user is dead-ended on a permanent server error.
**Fix:** on validation failure we now DEGRADE — mark the durable `quiz_jobs` row failed (so subsequent polls take the fatal-fast **422** the FE handles) and best-effort CLEAR the corrupt blob (new `CacheRepository.clear_final_result`, a raw json read/null/write that does NOT re-validate the whole graph state, so it can repair the very blob that fails validation), then return the 422 NOW instead of a 500. The question-validation path keeps its 500 (only the result path was the permanent loop).

**#3 — [LOW] Redis-outage removed every $ ceiling.**
All cost guards fail-open on the same Redis. Turnstile (Redis-independent, fail-closed) already bounds bot abuse, so this is **not directly exploitable**, but a sustained Redis outage left the live paid pipeline with *no* dollar cap.
**Fix:** a process-local in-memory fallback start cap (`local_fallback_limiter`) consulted ONLY when the $ counter read returns `None` (Redis down) on `/start`. **Degrade, not fail-closed:** a brief blip still admits real users; each replica enforces a coarse cap (cluster allowance during an outage = `N_replicas × cap`), and the cap evaporates the instant the counter read succeeds again. New config `redis_outage_local_start_cap` / `redis_outage_local_window_s`.

### PRECISION / CONSISTENCY / PRIVACY

**#4 — [LOW] Granular QF codes were unreachable for transient LLM failures.**
429 / timeout / oversized / provider-5xx all collapsed to `QF-UNKNOWN` at the `/quiz/start` inline catch-all, blunting triage.
**Fix:** `_qf_code_for_transient` reuses the EXACT classifiers the retry layers use (`litellm.RateLimitError` → `QF-LLM-RATE-LIMITED`, `Timeout`/`asyncio.TimeoutError` → `QF-AGENT-TIMEOUT`, `LLMResponseTooLargeError` → `QF-LLM-RESPONSE-TOO-LARGE`, other `_is_llm_transient` → `QF-LLM-PROVIDER-DOWN`); a non-transient/unclassifiable error keeps `QF-UNKNOWN` (no behaviour change). The `/proceed` + `/next` agent runs are in the background where `_finalize_durable_job` already classifies, so no change was needed there.

**#5 — [LOW] Middleware emitted the legacy envelope (no `code`/`whimsical`).**
The rate-limit (429) and body-size (413) middleware produce responses *without* raising an `HTTPException`, so they bypassed the coded-envelope handler and the FE's WhimsicalError could not render them.
**Fix:** new `build_coded_error_envelope` (resolves a QF spec and emits the same `code`/`whimsical` + legacy `errorCode` the handlers do, WITHOUT firing the support-notify on these high-volume infra paths), wired into all three middleware sites → `QF-RATE-LIMIT` (429) and `QF-PAYLOAD-TOO-LARGE` (413).

**#6 — [LOW] Raw client IP in two log lines.**
`quiz.py` `quiz_start.rate_limited` logged the raw IP and `rate_limit.py` `rate_limit.fail_open` logged the bucket key (which embeds the raw IP) — against the hashed-IP privacy posture.
**Fix:** both hash via the existing flag-HMAC `hash_ip` util (`quiz.py` logs `client_ip_hash`; `rate_limit.py` logs an HMAC-redacted key). Both helpers never raise.

**#7 — [LOW] `/feedback` 500 on a non-dict JSON body when Turnstile disabled.**
With Turnstile bypassed, the verify dependency doesn't coerce the body, so a top-level JSON array reached `body.pop(...)` and crashed with an `AttributeError` → 500.
**Fix:** guard the parsed body — a non-object yields a clean coded **400** (`QF-BAD-REQUEST`) before any pop/validate.

**#8 — [LOW] FAL URL allowlist empty-config silently disabled the SSRF boundary.**
An empty/cleared `image_gen.url_allowlist` made `_host_allowed` return `True` for any host ("allow all"), turning off the host check on FAL-returned URLs.
**Fix:** an empty/unset/all-blank allowlist now falls back to a SAFE built-in default (the canonical `fal.media` domains; the suffix rule covers `v2`/`v3`/`v3b`/* subdomains), and `_host_allowed([])` is treated as DENY. The configured default is unchanged — normal deployments are byte-for-byte identical; only the empty-config footgun is closed.

**#9 — [LOW] Turnstile `remoteip` bound to the spoofable left-most XFF hop.**
`_client_ip_for_remoteip` trusted the left-most `X-Forwarded-For` entry, which is attacker-controlled (ingresses APPEND the peer), partially defeating the token↔IP binding.
**Fix:** reuse the SAME trusted-hop resolver the rate limiter uses (`rate_limit._client_ip` + `TRUSTED_PROXY_HOPS`) — the hop counted from the RIGHT, validated as an IP, falling back to the peer on any anomaly, omitting `remoteip` on the `unknown` sentinel.

**#10 — [LOW] FE Dockerfile comment footgun.**
The build-stage comments claimed `COPY . .` includes `./frontend/.env` and that Vite loads it at build — but `.dockerignore` (correctly) EXCLUDES `.env`/`.env.*`. The misleading comment invited a future "fix" that un-ignores `.env` and bakes secrets (and any `VITE_`-prefixed value, which Vite inlines PUBLICLY) into the shipped bundle.
**Fix:** corrected the comments to state the exclusion is deliberate and warn against un-ignoring `.env`. No build behaviour change.

---

## How fail-OPEN was preserved

The hardening deliberately did **not** convert any Redis-dependent guard into a hard fail-closed control:

- **#1 reservation** — an `INCRBY` fault returns `None`; the request proceeds with nothing reserved (identical to the pre-existing read-only breaker's fail-open). The reservation is defense-in-depth behind the per-IP + per-session caps.
- **#3 local fallback** — engages ONLY when Redis is unreachable, enforces a *coarse* per-replica cap, and itself fails OPEN on any internal error; a Redis blip cannot DoS legitimate users, and the cap disappears the moment Redis returns.
- **#2 degrade** — turns a *permanent* 500 into the FE-handled fatal-fast 422; it does not block any healthy quiz.
- **#5 middleware** — additive envelope fields only; the legacy `errorCode` and status codes are unchanged.

## Optional / not taken (informational)

- `/feedback` rate limit is keyed by `quiz_id` only; adding an IP key would tighten it (minor).
- A duplicate/pre-baseline `/quiz/next` consumes action-cap budget though no paid run occurs (minor; could skip the cap increment on a duplicate).

---

## Validation

Implemented on `feat/security-hardening` (base `feat/perf-trio`), one commit per item group, each with unit/security tests. Full backend suite run per-directory (`tests/unit`, `tests/security`, `tests/api_modernization` separately, to avoid the known rate-limit cross-file pollution); ruff clean; `poetry check --lock` untouched (no dependency changes).
