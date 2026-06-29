# Remediation — branch `hardening/audit-2026-06-28`

Fixes for the audit in [AUDIT-2026-06-28.md](AUDIT-2026-06-28.md). Every finding
was skeptically re-verified (disprove-first) against **live Azure production
evidence** and a hard minimalism lens before any code was changed. This branch
implements the **P0 launch-blockers** plus the highest-value, lowest-risk P1s.

## What's fixed on this branch

| # | Severity | Fix | Files |
|---|----------|-----|-------|
| P0-1 | P0 | Per-`quiz_id` hard cap on cost-bearing agent actions; `/proceed` + `/next` can no longer drive unbounded paid LLM/FAL runs from one Turnstile solve | `backend/app/api/endpoints/quiz.py` |
| P0-2 | P0 | Trusted-proxy-aware client IP (right-most `X-Forwarded-For` hop, validated; `TRUSTED_PROXY_HOPS`) — defeats the proven XFF spoof that nullified per-IP limits; shared across rate_limit/content/topics (also closes the flag-quarantine DoS) | `backend/app/security/rate_limit.py`, `…/endpoints/content.py`, `topics.py` |
| P0-3 | P0 | One authoritative environment: OS `APP_ENVIRONMENT` wins over the baked YAML; `is_production()` / `NON_PROD_ENVS` treat unknown envs (incl. `azure`) as production so 2FA / weak-secret guard / HSTS fail **closed** | `backend/app/core/config.py`, `…/precompute/secrets.py`, `…/api/dependencies.py`, `tests/conftest.py` |
| P0-4 | P0 | Stop baking secrets into the image: `.dockerignore` excludes `.env*`; `COPY . .` → explicit allowlist (`app/` + `appconfig.local.yaml`). **Verified**: built image `/app` has no `.env` | `backend/.dockerignore`, `backend/Dockerfile` |
| P1 | P1 | Fail-closed Turnstile-enforcement startup assertion (prod) | `backend/app/services/precompute/secrets.py`, `backend/app/main.py` |
| P1 | P1 | iOS auto-zoom: floor topic-input font-size at 16px (was 15.2px) | `frontend/src/index.css` |
| P1 | P1 | Result-page dead-end: `GlobalErrorDisplay` honors `onHome` so the error/expired-result state always has a way out | `frontend/src/components/common/GlobalErrorDisplay.tsx` |
| P1 | P1 | Poll-timeout data loss: `408`/`poll_timeout` is now transient (resume polling) instead of fatally discarding the quiz | `frontend/src/store/quizStore.ts` |

> ⚠️ **Operational action required (P0-4):** rotate every credential that has
> lived in a Docker build context (OpenAI, Gemini, FAL, Cloudflare Turnstile
> secret, `OPERATOR_TOKEN`, `FLAG_HMAC_SECRET`, `PRECOMPUTE_HMAC_SECRET`).
>
> ⚠️ **Behavior change on next deploy (P0-3):** the app will resolve env to
> `azure` = production. Operator admin calls must send `X-Operator-2FA`;
> `OPERATOR_TOKEN`/`FLAG_HMAC_SECRET` must be ≥32 bytes (they are KV secrets);
> HSTS is now emitted. The live container already satisfies the Turnstile
> assertion (`ENABLE_TURNSTILE=true` + KV secret). If the edge proxy is **not**
> exactly Azure Container Apps' single ingress, set `TRUSTED_PROXY_HOPS`.

## Test the branch locally (no Docker required for tests)

**Backend** (uses `fakeredis` + `aiosqlite`, no real infra):
```bash
cd backend
./.venv312/Scripts/python.exe -m pytest -q --no-cov   # 1459 passed, 10 skipped
./.venv312/Scripts/python.exe -m ruff check app tests
```

**Frontend**:
```bash
cd frontend
npx vitest run
```

**Full stack (manual)** — Postgres + Redis in Docker, backend from the venv, Vite dev:
```bash
docker compose up -d db redis
cd backend && DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/quiz \
  REDIS_URL=redis://localhost:6379/0 APP_ENVIRONMENT=local ENABLE_TURNSTILE=false \
  ./.venv312/Scripts/python.exe -m uvicorn app.main:app --port 8000
cd frontend && npm run dev    # http://localhost:5173 (Turnstile bypassed locally)
```

**Docker image (P0-4 verification)**:
```bash
docker build -f backend/Dockerfile -t quizzical-backend:p0test backend/
docker run --rm --entrypoint sh quizzical-backend:p0test -c 'ls -A /app; test -f /app/.env && echo LEAK || echo "ok: no .env"'
```

## Not yet implemented (remaining P1/P2 — see AUDIT report)
Durable job queue for agent/image work; `TrustedHostMiddleware` wiring + a safe
default host allowlist; global daily/hourly USD spend ceiling on the live path;
nightly-promotion content/safety judge; max-question-cap finalization; Zod
deploy-skew resilience; per-result OG/SSR meta + analytics; etc. These are
larger or operationally sensitive and are tracked in the audit's P1/P2 sections.
