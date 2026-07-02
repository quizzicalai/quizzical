# quafel — Launch Punch List (2026-07-02)

Synthesis of a 6-lens **adversarial** pre-launch audit (security · UI/UX+a11y+content · performance+reliability · agent-quality+evals · pre-computed content · code-quality+tests+launch-ops), run on `main` after PRs #50–#62. Each finding was disprove-first verified with file:line. **Adversarial verdict: there are NO code-level launch blockers.** The only hard launch-blocker any lens raised — the "quafel is always lowercase" brand rule — is DONE (PRs #61 FE, #62 BE). Everything else is polish, hardening, or owner-operational.

Status: ✅ done this session · ◐ in progress · ⏳ staged (do if budget) · 🧑 human (in HUMAN-PUNCHLIST) · ⏸ deferred-with-rationale

## Launch-blockers
| Item | Status |
|------|--------|
| Brand "quafel" always lowercase (all user-facing FE + BE copy/meta/OG; X handle preserved) | ✅ #61 + #62 |

_No other lens found a launch-blocker._

## Owner-operational (can't/shouldn't do autonomously → HUMAN-PUNCHLIST, step-by-step)
| Item | Sev | Status |
|------|-----|--------|
| Rotate credentials (do last) — safe runbook provided | High | 🧑 |
| Prod Postgres `AllowAll` firewall (0.0.0.0/0) → restrict to Azure-services + ops IP | High | 🧑 (deploy uses it; needs care) |
| Verified DB TLS (asyncpg/psycopg cert-verify; traffic already TLS via Azure `require_secure_transport`) | Med | 🧑 |
| RESEND_API_KEY for support alerts | High | ✅ set in KV + wired live to container this session |
| `api-deploy.yml` RESEND passthrough (needs `workflow` scope I lack) | Low | 🧑 one-liner |
| CF-Connecting-IP / `TRUSTED_PROXY_HOPS=2` for accurate per-IP limits behind CF | Med | 🧑 |
| Operator "2FA" is presence-check only (not TOTP) | Med | 🧑 (rename or implement) |
| Confirm Azure PG tier `max_connections` ≥ pool (60) or lower pool_size | Med | 🧑 |

## Executed this session (beyond the two brand PRs)
| Item | Status |
|------|--------|
| Home spacing before Popular list; click-tooltip "quafel uses AI…" (InfoTip); hint grey/italic/smaller | ✅ #61 |
| a11y: QuestionView h2 double-announce removed | ✅ #61 |
| Feedback "optional" vs required contradiction | ✅ #61 |
| Perf: merge 3 Google-Fonts→1; wrap index.html localStorage (Safari private); delete dead Image.tsx | ✅ #61 |
| Cold /quiz/start landing escape hatch (30s) — "no hung UX" | ✅ #61 |
| Dropped vestigial policy_status column from prod (backup-first) | ✅ prior session |
| CF API token verified working from this IP; RESEND live | ✅ |

## High-value, executing now / staged (not blockers)
> **2026-07-02 (evening) update:** P1–P11 + P13 ALL LANDED (PRs #64–#74); statuses below updated in place. See also `DEEP-REVIEW-PUNCHLIST-2026-07-02.md` (the second, deeper audit — incl. the CRITICAL answer-shuffle record bug fixed in PR #66).

| # | Lens | Item | Sev | Status |
|---|------|------|-----|--------|
| P1 | perf | Precompute **durability**: rehost FAL-CDN-only images into `media_assets` | High | ✅ #72 — ALL ~1589 rehosted; 0 `fal.media` URLs left; importer anti-clobber guard |
| P2 | precompute | Backfill imageless characters | Med | ✅ #72 — 1405/1443 backfilled with the fixed object-vs-person prompts (~$19 FAL); 38 FAL-refusals retryable |
| P3 | precompute | Zero-question packs; broken `grimm-fairy-tale-archetype` | Med | ✅/🧑 — grimm retired in prod; 20/24 packs regenerated+judge-gated+signed; **owner runs the one-command import** (HUMAN-PUNCHLIST §B); 4 stragglers follow-up |
| P4 | agent | `final_profile_writer` never validated; PBW ungrounded | High | ✅ #70 — FPW **4.68** (floor 4.2, first live validation); PBW canonical grounding added (score still content-capped at 2.20 — honest gap documented) |
| P5 | agent | NQG phrase-pool token waste on the hot loop | High(cost) | ✅ #70 — pool inlining deleted; NQG p95 6.6s→**2.4s** (CoT override also removed on eval evidence) |
| P6 | agent | FORBIDDEN block dropped from QG/NQG overrides | High | ✅ #70 — overrides removed entirely; code defaults (which carry the block) win on eval; regression test pins the production-resolved prompt |
| P7 | agent | Evals tested DEFAULT_PROMPTS, not shipped overrides | High | ✅ #70 — `prompts_adapter` renders the production-effective prompt |
| P8 | agent | Empty-but-named profile can ship | Med | ✅ #70 — blank batch profile treated as missing → per-character fallback regenerates |
| P9 | perf | `/proceed` + `/next` Redis-miss rehydrate | High | ✅ #67 — shared `_load_state_with_db_fallback`; 404 only when the DB has no row either |
| P10 | perf | PG `statement_timeout` | High | ✅ #67 — driver-branched (psycopg `options` / asyncpg `server_settings`), configurable, sqlite untouched |
| P11 | perf | Precompute serve path query waste | Med | ✅ #67 — dead empty-Link block deleted; full HydratedPack cached in Redis (repeat-hit = 1 GET), invalidated on import |
| P12 | agent | CF edge: bot-UA `/result/*` → `/result-meta` | Med | ◐ still staged (CF token available; PUBLIC_SITE_URL hardening landed in #73) |
| P13 | eval | No full-flow e2e | — | ✅ `fullFlow.live.spec.ts` (3 scenarios, `RUN_LIVE_E2E=1`, manual by design) |

## Test / code hygiene (not blockers)
| # | Item | Status |
|---|------|--------|
| T1 | 6 orphaned Playwright CT specs | ✅ #67 — wired into `playwright-ct.config.ts`, 18/18 pass; linux baselines generated in the Playwright container; 2% cross-env pixel tolerance |
| T2 | No browser e2e gates the FE deploy; add chromium CT to SWA quality_gate | 🧑 workflow-scope-blocked (fe-ci runs CT now; the SWA gate line needs the owner's `workflow`-scoped push) |
| T3 | No `--cov-fail-under=85` in the deploy pytest step | 🧑 workflow-scope-blocked |
| T4 | "Windows-only" cross-file flake | ✅ #67 — REAL root cause found (fixtures captured a stale `app.main` after `test_trusted_host`'s reload → overrides silently lost → live-Redis bucket drain); lazy app resolution + `__init__.py`s; full combined run 2034/2034 |
| C1 | Dead `QF_BLOCKED_CATEGORY` error code | ✅ #67 deleted |
| C2 | Prompt-injection wrappers (`wrap_user_input`) test-only wired — decide wire-in vs delete | ⏳ |
| C3 | ~12 test-only orphaned precompute-build modules | ⏸ documented |
| C4 | `.DS_Store` untracked; root README expanded | ✅ #67 |
| C5 | ~53 silent `except Exception: pass`; god-file quiz.py; duplicate normalization | ⏸ elegance, deferred |

## Verified-GOOD (adversarially confirmed, no action) — abridged
Turnstile fail-closed + IP-bound; XFF anti-spoof; per-quiz/IP caps + $-breaker on all paid paths; hard 24-question cap; FAL fan-out cap; operator constant-time auth + HMAC import; strict API CSP/HSTS/nosniff/TrustedHost; FE CSP no unsafe-inline; no SQLi/path-traversal; input hardening (NUL/control/bidi/caps); config-first-paint (no /config gate); cheap status poll; all-or-none images; Redis fail-open guards; strong CD (auth-ping, KV-verify, Trivy, live smoke, Turnstile validation, auto-rollback); complete launch hygiene (favicon/og/manifest/robots/sitemap/llms.txt/404). Safety-removal was symbol-clean. `.env` quote-wrapping is a non-issue (dotenv strips quotes; prod uses literal KV env).

## Bottom line for launch
Code is launch-ready (no blockers; brand rule satisfied). The pre-launch must-dos are **owner-operational** (rotate keys, lock the DB firewall) — in the human punch list with steps. The remaining engineering items are quality/perf/agent improvements being executed or staged below; none gate a soft launch.
