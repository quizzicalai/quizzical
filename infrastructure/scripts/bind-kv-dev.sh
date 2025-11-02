#!/usr/bin/env bash
# infrastructure/scripts/bind-kv-dev.sh

set -euo pipefail

# =====================================================================================
# Quizzical (DEV): Bind Container App to Key Vault secrets via managed identity
# Script location: infrastructure/scripts/bind-kv-dev.sh
# Repo-relative files used (if present):
#   - backend/.env
#   - frontend/.env    (frontend-only keys are NOT written to KV; for reference only)
# =====================================================================================

# ---- Resolve repo paths (relative to this script) -----------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_ENV="${REPO_ROOT}/backend/.env"
FRONTEND_ENV="${REPO_ROOT}/frontend/.env"

echo "== Script dir: ${SCRIPT_DIR}"
echo "== Repo root : ${REPO_ROOT}"
echo "== backend/.env : ${BACKEND_ENV} $( [[ -f "${BACKEND_ENV}" ]] && echo '(found)' || echo '(missing)' )"
echo "== frontend/.env: ${FRONTEND_ENV} $( [[ -f "${FRONTEND_ENV}" ]] && echo '(found)' || echo '(missing)' )"

# ---- Target Azure resources (override with env vars if needed) ----------------------
RG="${RG:-rg-quizzical-shared}"
APP="${APP:-api-quizzical-dev}"
SUB="${SUB:-$(az account show --query id -o tsv)}"
KV="${KV:-}"   # set KV=<name> to force a specific vault if your RG has multiple

# This sets FastAPI's APP_ENVIRONMENT env var on the Container App.
# Use "local" for local compose, "azure" (or anything not "local") for ACA.
APP_ENV="${APP_ENV:-azure}"

LOG_PROFILE="${LOG_PROFILE:-perf}"
LOG_LEVEL_ROOT="${LOG_LEVEL_ROOT:-INFO}"
LOG_LEVEL_APP="${LOG_LEVEL_APP:-INFO}"
LOG_LEVEL_LIBS="${LOG_LEVEL_LIBS:-WARNING}"

echo "== Subscription: ${SUB}"
az account set -s "$SUB"

# ---- Simple .env reader (no export) -------------------------------------------------
# Reads KEY=VALUE (ignores comments/blank lines) and prints value for requested key.
get_env_val() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || { echo ""; return; }
  # Take last assignment; strip quotes (single or double).
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n1 | sed -E 's/^[[:space:]]*'"${key}"'[[:space:]]*=[[:space:]]*//')"
  line="${line%\"}" ; line="${line#\"}"
  line="${line%\'}" ; line="${line#\'}"
  echo "$line"
}

# ---- Pull useful values from backend/.env (if present) -------------------------------
# CORS
BACK_ALLOWED_ORIGINS_LOCAL="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS")"
BACK_ALLOWED_ORIGINS_AZURE_DEV="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS__AZURE_DEV")"

# DB
BACK_DATABASE_URL="$(get_env_val "$BACKEND_ENV" "DATABASE_URL")"
BACK_DATABASE_URL_NESTED="$(get_env_val "$BACKEND_ENV" "DATABASE__URL")"
BACK_DB_USER="$(get_env_val "$BACKEND_ENV" "DATABASE__USER")"
BACK_DB_PASS="$(get_env_val "$BACKEND_ENV" "DATABASE_PASSWORD")"
BACK_DB_HOST="$(get_env_val "$BACKEND_ENV" "DATABASE__HOST")"
BACK_DB_PORT="$(get_env_val "$BACKEND_ENV" "DATABASE__PORT")"
BACK_DB_NAME="$(get_env_val "$BACKEND_ENV" "DATABASE__DB_NAME")"

# Redis
BACK_REDIS_URL="$(get_env_val "$BACKEND_ENV" "REDIS_URL")"
BACK_REDIS_HOST="$(get_env_val "$BACKEND_ENV" "REDIS__HOST")"
BACK_REDIS_PORT="$(get_env_val "$BACKEND_ENV" "REDIS__PORT")"
BACK_REDIS_DB="$(get_env_val "$BACKEND_ENV" "REDIS__DB")"

# Secrets
BACK_SECRET_KEY="$(get_env_val "$BACKEND_ENV" "SECRET_KEY")"
BACK_OPENAI_API_KEY="$(get_env_val "$BACKEND_ENV" "OPENAI_API_KEY")"
BACK_FAL_AI_KEY="$(get_env_val "$BACKEND_ENV" "FAL_AI_KEY")"
BACK_GROQ_API_KEY="$(get_env_val "$BACKEND_ENV" "GROQ_API_KEY")"
BACK_TURNSTILE_SECRET_KEY="$(get_env_val "$BACKEND_ENV" "TURNSTILE_SECRET_KEY")"

# Feature flags
BACK_ENABLE_TURNSTILE="$(get_env_val "$BACKEND_ENV" "ENABLE_TURNSTILE")"

# Defaults for local-only
DEFAULT_ALLOWED_DEV='["http://localhost:5173","http://127.0.0.1:5173","http://localhost:3000","http://127.0.0.1:3000"]'

# Final ALLOWED_ORIGINS decision order (highest to lowest):
#  1) Explicit shell override: ALLOWED_ORIGINS='[...]' ./bind-kv-dev.sh
#  2) If APP_ENV != local and backend has ALLOWED_ORIGINS__AZURE_DEV, use it
#  3) backend ALLOWED_ORIGINS (local)
#  4) DEFAULT_ALLOWED_DEV (local)
ALLOWED_OVERRIDE="${ALLOWED_ORIGINS:-}"
APP_ENV_NORM="$(echo "${APP_ENV}" | tr '[:upper:]' '[:lower:]')"

if [[ -n "${ALLOWED_OVERRIDE}" ]]; then
  ALLOWED_ORIGINS_EFFECTIVE="${ALLOWED_OVERRIDE}"
elif [[ "${APP_ENV_NORM}" != "local" && -n "${BACK_ALLOWED_ORIGINS_AZURE_DEV}" ]]; then
  ALLOWED_ORIGINS_EFFECTIVE="${BACK_ALLOWED_ORIGINS_AZURE_DEV}"
else
  ALLOWED_ORIGINS_EFFECTIVE="${BACK_ALLOWED_ORIGINS_LOCAL:-$DEFAULT_ALLOWED_DEV}"
fi

# Compose DATABASE_URL if nested values present but flat URL missing
if [[ -z "${BACK_DATABASE_URL:-}" && -z "${BACK_DATABASE_URL_NESTED:-}" ]]; then
  if [[ -n "${BACK_DB_USER:-}" && -n "${BACK_DB_PASS:-}" && -n "${BACK_DB_HOST:-}" && -n "${BACK_DB_PORT:-}" && -n "${BACK_DB_NAME:-}" ]]; then
    BACK_DATABASE_URL_NESTED="postgresql+asyncpg://${BACK_DB_USER}:${BACK_DB_PASS}@${BACK_DB_HOST}:${BACK_DB_PORT}/${BACK_DB_NAME}"
  fi
fi

# Compose REDIS_URL if host/port/db present but flat URL missing
if [[ -z "${BACK_REDIS_URL:-}" ]]; then
  if [[ -n "${BACK_REDIS_HOST:-}" && -n "${BACK_REDIS_PORT:-}" ]]; then
    BACK_REDIS_URL="redis://${BACK_REDIS_HOST}:${BACK_REDIS_PORT}/${BACK_REDIS_DB:-0}"
  fi
fi

# ---- Locate Key Vault (or use provided KV) ------------------------------------------
if [[ -z "${KV}" ]]; then
  set +e
  KV_LIST=$(az keyvault list -g "$RG" --query "[].name" -o tsv)
  set -e
  if [[ -z "$KV_LIST" ]]; then
    echo "!! No Key Vaults found in resource group $RG. Create one or set KV=<name> and rerun."
    exit 1
  fi
  KV="$(echo "$KV_LIST" | head -n1)"
fi

KV_ID="$(az keyvault show -n "$KV" -g "$RG" --query id -o tsv)"
KV_URI="https://${KV}.vault.azure.net"
echo "== Using Key Vault: $KV  ($KV_URI)"

# ---- Ensure Container App has a system-managed identity ------------------------------
echo "== Assigning system-managed identity to ${APP} (idempotent)"
az containerapp identity assign -g "$RG" -n "$APP" --system-assigned >/dev/null || true

APP_PRINCIPAL_ID="$(az containerapp show -g "$RG" -n "$APP" --query identity.principalId -o tsv)"
echo "== App principalId: $APP_PRINCIPAL_ID"

# ---- Grant KV read (RBAC) -----------------------------------------------------------
echo "== Granting 'Key Vault Secrets User' on the vault to the app identity (idempotent)"
az role assignment create \
  --assignee-object-id "$APP_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" \
  --scope "$KV_ID" >/dev/null || true

# ---- Helper: set Key Vault secret (create or update) --------------------------------
kv_put() {
  local name="$1" value="${2:-}"
  if [[ -n "$value" ]]; then
    echo "   - Setting KV secret: ${name}"
    az keyvault secret set --vault-name "$KV" --name "$name" --value "$value" >/dev/null
  else
    echo "   - Skipping KV secret (no value): ${name}"
  fi
}

echo "== Creating/updating Key Vault secrets from backend/.env"
kv_put "secret-key"            "${BACK_SECRET_KEY:-}"
kv_put "database-url"          "${BACK_DATABASE_URL:-${BACK_DATABASE_URL_NESTED:-}}"
kv_put "redis-url"             "${BACK_REDIS_URL:-}"
kv_put "openai-api-key"        "${BACK_OPENAI_API_KEY:-}"
kv_put "fal-ai-key"            "${BACK_FAL_AI_KEY:-}"
kv_put "groq-api-key"          "${BACK_GROQ_API_KEY:-}"
kv_put "turnstile-secret-key"  "${BACK_TURNSTILE_SECRET_KEY:-}"

# ---- Register KV references as Container App secrets (create or update) -------------
echo "== Registering Container App secret references to Key Vault (idempotent)"
az containerapp secret set -g "$RG" -n "$APP" --secrets \
  secret-key=keyvaultref:"$KV_URI/secrets/secret-key",identityref:system \
  database-url=keyvaultref:"$KV_URI/secrets/database-url",identityref:system \
  redis-url=keyvaultref:"$KV_URI/secrets/redis-url",identityref:system \
  openai-api-key=keyvaultref:"$KV_URI/secrets/openai-api-key",identityref:system \
  fal-ai-key=keyvaultref:"$KV_URI/secrets/fal-ai-key",identityref:system \
  groq-api-key=keyvaultref:"$KV_URI/secrets/groq-api-key",identityref:system \
  turnstile-secret-key=keyvaultref:"$KV_URI/secrets/turnstile-secret-key",identityref:system >/dev/null || {
    echo "!! Warning: secret set returned non-zero; check Container App extension version and RBAC."
  }

# ---- Update Container App env (bind secret refs + non-secrets) ----------------------
# ENABLE_TURNSTILE: use backend file if present, otherwise default to 'true' in Azure (non-local).
ENABLE_TS="${BACK_ENABLE_TURNSTILE:-}"
if [[ -z "$ENABLE_TS" ]]; then
  if [[ "${APP_ENV_NORM}" == "local" ]]; then ENABLE_TS="false"; else ENABLE_TS="true"; fi
fi

echo "== Updating env vars on Container App (idempotent)"
az containerapp update -g "$RG" -n "$APP" --set-env-vars \
  SECRET_KEY=secretref:secret-key \
  DATABASE_URL=secretref:database-url \
  DATABASE__URL=secretref:database-url \
  REDIS_URL=secretref:redis-url \
  OPENAI_API_KEY=secretref:openai-api-key \
  FAL_AI_KEY=secretref:fal-ai-key \
  GROQ_API_KEY=secretref:groq-api-key \
  TURNSTILE_SECRET_KEY=secretref:turnstile-secret-key \
  ENABLE_TURNSTILE="${ENABLE_TS}" \
  PROJECT__API_PREFIX=/api/v1 \
  APP_ENVIRONMENT="${APP_ENV}" \
  LOG_PROFILE="${LOG_PROFILE}" \
  LOG_LEVEL_ROOT="${LOG_LEVEL_ROOT}" \
  LOG_LEVEL_APP="${LOG_LEVEL_APP}" \
  LOG_LEVEL_LIBS="${LOG_LEVEL_LIBS}" \
  ALLOWED_ORIGINS="${ALLOWED_ORIGINS_EFFECTIVE}" >/dev/null

echo "== Done."
echo "NOTE: Restart is recommended after secret/env changes."
echo "  az containerapp restart -g ${RG} -n ${APP}"
echo "Verify with:"
echo "  az containerapp secret list -g ${RG} -n ${APP}"
echo "  az containerapp show -g ${RG} -n ${APP} --query \"properties.template.containers[0].env\""
echo "  FQDN=\$(az containerapp show -g ${RG} -n ${APP} --query \"properties.configuration.ingress.fqdn\" -o tsv)"
echo "  curl -sS -D - \"https://\${FQDN}/config\" -H 'Origin: https://kind-smoke-0ca2ff21e.3.azurestaticapps.net' -o /dev/null"
