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
| # | Lens | Item | Sev | Status |
|---|------|------|-----|--------|
| P1 | perf | Precompute **durability**: 1573 images are FAL-CDN-only, `media_assets=0`; rehost path must be BUILT (not "never run"). Failure mode = unbudgeted live-regen + art drift, not instant 404. | High | ◐ building rehost ($0 FAL) |
| P2 | precompute | Backfill ~1616 imageless characters (~$18 FAL, funded $65) | Med | ⏳ |
| P3 | precompute | 16 packs have 0 baseline questions; `grimm-fairy-tale-archetype` has 0 characters (broken) | Med | ⏳ unpublish/regen |
| P4 | agent | `final_profile_writer` (4.2 floor) never validated live; `profile_batch_writer` 2.81 (root cause: `character_contexts={}` — no grounding) | High | ⏳ eval + grounding |
| P5 | agent | `next_question_generator` inlines a phrase-pool + reads a `progress_phrase` field no prompt emits → wasted tokens on the hot loop | High(cost) | ⏳ |
| P6 | agent | Config `llm.prompts` overrides DROP the anti-self-referential FORBIDDEN block (runtime guard still catches it) | High | ⏳ |
| P7 | agent | `prompts_adapter` evals read DEFAULT_PROMPTS, not the shipped `llm.prompts` overrides → evals test different text than prod | High | ⏳ |
| P8 | agent | Empty-but-named profile can ship (fallback treats it as present) | Med | ⏳ |
| P9 | perf | `/proceed` + `/next` don't rehydrate from Postgres on a Redis miss (→ mid-quiz dead-end); `/status` already does | High | ⏳ |
| P10 | perf | No PG `statement_timeout` (driver = psycopg → `options=-c statement_timeout=15000`) | High | ⏳ (driver-specific, verify) |
| P11 | perf | Precompute serve path does ~9 serial queries + a wasted empty-Link query; cache full HydratedPack | Med | ⏳ (owner "perf via precompute") |
| P12 | agent | CF edge: bot-UA `/result/*` → `/result-meta` for rich cards + AI-crawlability | Med | ◐ CF worker (token now available) |
| P13 | eval | No full-flow e2e (topic→…→share/feedback/restart) in the eval set; per-function only | — | ◐ building (manual/live-gated) |

## Test / code hygiene (not blockers)
| # | Item | Status |
|---|------|--------|
| T1 | 6 orphaned Playwright CT specs (`tests/ct/**` incl. LandingPage/QuizFlowPage) run in no config/workflow | ⏳ wire or delete |
| T2 | No browser e2e gates the FE deploy; add chromium CT to SWA quality_gate | ⏳ |
| T3 | No `--cov-fail-under=85` in the deploy pytest step (coverage informational) | ⏳ |
| T4 | Windows-only cross-file flake (`test_input_validation` dup basename; add `tests/unit/**/__init__.py`) — NOT CI-real | ⏳ |
| C1 | Dead `QF_BLOCKED_CATEGORY` error code (post-safety-removal) | ⏳ delete |
| C2 | Prompt-injection wrappers (`wrap_user_input`) are test-only wired (dead or disconnected) — decide wire-in vs delete | ⏳ |
| C3 | ~12 test-only orphaned precompute-build modules (build pipeline is script-driven; serve path is live) | ⏸ documented |
| C4 | `git rm --cached .DS_Store`; expand 2-line root README | ⏳ |
| C5 | ~53 silent `except Exception: pass` (add logging); god-file quiz.py; duplicate normalization | ⏸ elegance, deferred |

## Verified-GOOD (adversarially confirmed, no action) — abridged
Turnstile fail-closed + IP-bound; XFF anti-spoof; per-quiz/IP caps + $-breaker on all paid paths; hard 24-question cap; FAL fan-out cap; operator constant-time auth + HMAC import; strict API CSP/HSTS/nosniff/TrustedHost; FE CSP no unsafe-inline; no SQLi/path-traversal; input hardening (NUL/control/bidi/caps); config-first-paint (no /config gate); cheap status poll; all-or-none images; Redis fail-open guards; strong CD (auth-ping, KV-verify, Trivy, live smoke, Turnstile validation, auto-rollback); complete launch hygiene (favicon/og/manifest/robots/sitemap/llms.txt/404). Safety-removal was symbol-clean. `.env` quote-wrapping is a non-issue (dotenv strips quotes; prod uses literal KV env).

## Bottom line for launch
Code is launch-ready (no blockers; brand rule satisfied). The pre-launch must-dos are **owner-operational** (rotate keys, lock the DB firewall) — in the human punch list with steps. The remaining engineering items are quality/perf/agent improvements being executed or staged below; none gate a soft launch.
