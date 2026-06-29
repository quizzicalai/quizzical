# Azure Infrastructure & Ops Reference — Quafel / Quizzical

_Captured 2026-06-29 from live `az` CLI + GitHub investigation so these don't need re-running. Secret **values** are never recorded here — only names/locations. Update this doc when infra changes._

## Resources (all in resource group `rg-quizzical-shared`)

| Resource | Type | Key facts |
|---|---|---|
| `api-quizzical-dev` | Container App (API) | FQDN `api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io`. Env: `APP_ENVIRONMENT=azure` (→ `is_production()` true), `ALLOWED_ORIGINS=["https://kind-smoke-0ca2ff21e.3.azurestaticapps.net","https://quafel.com","https://www.quafel.com"]`, `PUBLIC_SITE_URL=https://quafel.com`. Single uvicorn→gunicorn (`-w ${WEB_CONCURRENCY:-1}`, exec, STOPSIGNAL SIGTERM). |
| `redis-quizzical-dev` | Container App (Redis) | **Internal ingress** (`*.internal.…`), image `redis:8-alpine`, 0.5 CPU / 1Gi, **minReplicas:0 (scales to zero when idle!)**, **no volume (ephemeral)**. NOT Azure Cache for Redis. |
| `pg-quizzical-dev` | Postgres Flexible Server | Burstable `Standard_B1ms`, 32 GB, backup **retention 7 days**, **geoRedundantBackup Disabled** (cost-minimal; backup storage within the free allowance). DB `quiz`. |
| `quizzical-shared-kv` | Key Vault | Secrets: `database-url`, `redis-url`, `openai-api-key`, `gemini-api-key`, `groq-api-key`, `fal-ai-key`, `turnstile-secret-key`, `operator-token`, `flag-hmac-secret`, `precompute-hmac-secret`, `secret-key`, + legacy `Services--*--ApiKey` duplicates. |
| `swa-quizzical-dev` | Static Web App (frontend) | Default host `kind-smoke-0ca2ff21e.3.azurestaticapps.net`; custom domains **`quafel.com` + `www.quafel.com` (both Ready)**. Build env `VITE_API_BASE_URL`, `VITE_PUBLIC_URL=https://quafel.com`. |

## Redis & the durable checkpointer (resolved investigation — do not repeat)
- LangGraph `AsyncRedisSaver` needs **RedisJSON + RediSearch** modules (it issues `JSON.SET` / `FT.CREATE`). `redis:8-alpine` lacks them → init fails with `unknown command FT._LIST` → the app **falls back to `InMemorySaver`** (logged loudly at ERROR in prod by design, graph.py).
- Swapping the Redis container to `redis/redis-stack-server:latest` **did NOT expose RediSearch** in this Container App (still `FT._LIST` unknown), and the container is `minReplicas:0` (scales to zero). **Reverted to `redis:8-alpine`.**
- A truly durable checkpointer needs: `minReplicas:1` (always-on, ~$15–25/mo) **+** a module-verified image (pin a known-good redis-stack tag and confirm `MODULE LIST` shows `search`+`ReJSON`, or Azure Managed Redis Enterprise w/ modules) **+** a persistent volume. **Deferred** by the owner — the Postgres `quiz_jobs` crash-recovery layer covers process death for free.

## Deploy mechanisms (IMPORTANT)
- **API** — `.github/workflows/api-deploy.yml` → `azure/container-apps-deploy-action`. Non-secret env is set by `infrastructure/scripts/sync-nonsecret-env-dev.sh` (`APP_ENVIRONMENT`, `ALLOWED_ORIGINS`, `PUBLIC_SITE_URL`, …) via `az containerapp update --set-env-vars`; secrets bound from Key Vault (`bind-kv-dev.sh`); DB schema applied idempotently from `backend/db/init/init.sql` on deploy. Post-deploy gates: **Trivy + live-smoke (headers) + turnstile-validation**, with **auto-rollback** on failure. Concurrency: pushes cancel in-progress (so rapid merges → only the last deploy runs).
- **`infrastructure/main.bicep` is subscription-scoped** (RG + Key Vault + App Config only) — it does NOT define/deploy the Container App. So any bicep autoscale/replica policy is **advisory**; apply imperatively via `az containerapp update`.
- **Frontend** — `.github/workflows/azure-static-web-apps-kind-smoke-0ca2ff21e.yml` builds (with `VITE_API_BASE_URL` + `VITE_PUBLIC_URL`) and deploys to the SWA. Triggers on push to `main` (frontend paths). `frontend/public/` is copied verbatim into `dist/` (so brand assets there are served directly).

## GitHub secrets (present — verified)
`AZURE_CLIENT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_STATIC_WEB_APPS_API_TOKEN_KIND_SMOKE_0CA2FF21E`, `FAL_AI_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPERATOR_TOKEN`, `PG_USER`, `PG_PASS`, `FLAG_HMAC_SECRET`, `PRECOMPUTE_HMAC_SECRET`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`. (So the nightly promote job's `GEMINI_API_KEY` resolves — no action needed.)

## Backups (verified — already cost-minimal)
- **Local:** Windows scheduled task **"Quizzical Prod DB Backup"** (nightly ~03:00) runs `backend/scripts/backup_prod_db.ps1` → custom-format `pg_dump` into `backend/backups/` (**gitignored**), pruned to 7 days. Runs only when the PC is on.
- **Azure:** 7-day PITR, geo-redundancy off → within the free backup allowance. Nothing to cut.

## One-off DB access for ops
Add a temporary firewall rule for the current public IP, connect with asyncpg using the `database-url` KV secret (strip the `+driver`, `ssl="require"`), then **ALWAYS remove the rule**:
```
az postgres flexible-server firewall-rule create -g rg-quizzical-shared -n pg-quizzical-dev --rule-name claude-temp --start-ip-address <IP> --end-ip-address <IP>
# … do work …
az postgres flexible-server firewall-rule delete -g rg-quizzical-shared -n pg-quizzical-dev --rule-name claude-temp --yes
```
(Note: a broad `AllowAll_2025-11-2_…` rule + `AllowAllAzureIPs`/`AllowAllAzureServices` also exist.) A full one-off JSON backup was taken to `backend/backups/prod-db-backup-2026-06-29.json` (17 tables / 14,141 rows).

## Ready-to-paste `az` commands
- **Autoscale (deferred, apply when traffic warrants):** `az containerapp update -g rg-quizzical-shared -n api-quizzical-dev --min-replicas 1 --max-replicas 5` (then add an HTTP/concurrency scale rule).
- **API logs:** `az containerapp logs show -g rg-quizzical-shared -n api-quizzical-dev --type console --tail 200`
- **Set/verify a non-secret env var:** prefer editing `infrastructure/scripts/sync-nonsecret-env-dev.sh` (persists across deploys) over a one-off `--set-env-vars` (a later deploy overwrites it).
