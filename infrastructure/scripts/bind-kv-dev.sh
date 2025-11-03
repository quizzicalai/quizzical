#!/usr/bin/env bash
# infrastructure/scripts/bind-kv-dev.sh
set -euo pipefail

# =====================================================================================
# Bind Container App to Key Vault secrets via managed identity (authoritative)
# =====================================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_ENV="${REPO_ROOT}/backend/.env"
FRONTEND_ENV="${REPO_ROOT}/frontend/.env"

RG="${RG:-rg-quizzical-shared}"
APP="${APP:-api-quizzical-dev}"
SUB="${SUB:-$(az account show --query id -o tsv)}"
KV="${KV:-}"                 # Optionally set KV=<vault-name>
APP_ENV="${APP_ENV:-azure}"  # 'azure' for ACA
LOG_PROFILE="${LOG_PROFILE:-perf}"
LOG_LEVEL_ROOT="${LOG_LEVEL_ROOT:-INFO}"
LOG_LEVEL_APP="${LOG_LEVEL_APP:-INFO}"
LOG_LEVEL_LIBS="${LOG_LEVEL_LIBS:-WARNING}"
PROTECT_EXISTING="${PROTECT_EXISTING:-1}"  # don't overwrite existing KV secrets unless set to 0

echo "== Subscription: ${SUB}"
az account set -s "$SUB" 1>/dev/null

get_env_val() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || { echo ""; return; }
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n1 | sed -E 's/^[[:space:]]*'"${key}"'[[:space:]]*=[[:space:]]*//')"
  line="${line%\"}" ; line="${line#\"}"
  line="${line%\'}" ; line="${line#\'}"
  echo "$line"
}

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

DEFAULT_ALLOWED_DEV='["http://localhost:5173","http://127.0.0.1:5173","http://localhost:3000","http://127.0.0.1:3000"]'
ALLOWED_OVERRIDE="${ALLOWED_ORIGINS:-}"
APP_ENV_NORM="$(echo "${APP_ENV}" | tr '[:upper:]' '[:lower:]')"

# Avoid pushing localhost-style URLs to Azure
is_local_pg()   { [[ "$1" =~ @([lL]ocalhost|127\.0\.0\.1|db)(:|/|$) ]]; }
is_local_redis(){ [[ "$1" =~ ^redis://(localhost|127\.0\.0\.1|redis): ]]; }

if [[ -n "${ALLOWED_OVERRIDE}" ]]; then
  ALLOWED_ORIGINS_EFFECTIVE="${ALLOWED_OVERRIDE}"
elif [[ "${APP_ENV_NORM}" != "local" && -n "${BACK_ALLOWED_ORIGINS_AZURE_DEV}" ]]; then
  ALLOWED_ORIGINS_EFFECTIVE="${BACK_ALLOWED_ORIGINS_AZURE_DEV}"
else
  ALLOWED_ORIGINS_EFFECTIVE="${BACK_ALLOWED_ORIGINS_LOCAL:-$DEFAULT_ALLOWED_DEV}"
fi

# Compose DB/Redis URLs if needed
if [[ -z "${BACK_DATABASE_URL:-}" && -z "${BACK_DATABASE_URL_NESTED:-}" ]]; then
  if [[ -n "${BACK_DB_USER:-}" && -n "${BACK_DB_PASS:-}" && -n "${BACK_DB_HOST:-}" && -n "${BACK_DB_PORT:-}" && -n "${BACK_DB_NAME:-}" ]]; then
    BACK_DATABASE_URL_NESTED="postgresql+asyncpg://${BACK_DB_USER}:${BACK_DB_PASS}@${BACK_DB_HOST}:${BACK_DB_PORT}/${BACK_DB_NAME}"
  fi
fi
if [[ -z "${BACK_REDIS_URL:-}" && -n "${BACK_REDIS_HOST:-}" && -n "${BACK_REDIS_PORT:-}" ]]; then
  BACK_REDIS_URL="redis://${BACK_REDIS_HOST}:${BACK_REDIS_PORT}/${BACK_REDIS_DB:-0}"
fi

# Don't allow local URLs into Azure environment
if [[ "${APP_ENV_NORM}" != "local" ]]; then
  if [[ -n "${BACK_DATABASE_URL:-}" ]] && is_local_pg "${BACK_DATABASE_URL}"; then BACK_DATABASE_URL=""; fi
  if [[ -n "${BACK_DATABASE_URL_NESTED:-}" ]] && is_local_pg "${BACK_DATABASE_URL_NESTED}"; then BACK_DATABASE_URL_NESTED=""; fi
  if [[ -n "${BACK_REDIS_URL:-}" ]] && is_local_redis "${BACK_REDIS_URL}"; then BACK_REDIS_URL=""; fi
fi

# Locate Key Vault
if [[ -z "${KV}" ]]; then
  KV_LIST="$(az keyvault list -g "$RG" --query "[].name" -o tsv)"
  [[ -n "$KV_LIST" ]] || { echo "!! No Key Vaults in $RG"; exit 1; }
  KV="$(echo "$KV_LIST" | head -n1)"
fi
KV_ID="$(az keyvault show -n "$KV" -g "$RG" --query id -o tsv)"
KV_URI="https://${KV}.vault.azure.net"
echo "== Using Key Vault: $KV ($KV_URI)"

# Ensure system-assigned identity
az containerapp identity assign -g "$RG" -n "$APP" --system-assigned >/dev/null || true
APP_PRINCIPAL_ID="$(az containerapp show -g "$RG" -n "$APP" --query identity.principalId -o tsv)"
echo "== App principalId: $APP_PRINCIPAL_ID"

# Grant vault read (RBAC)
az role assignment create \
  --assignee-object-id "$APP_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" \
  --scope "$KV_ID" >/dev/null || true

kv_get() { az keyvault secret show --vault-name "$KV" --name "$1" --query value -o tsv 2>/dev/null || true; }
kv_put() {
  local name="$1" value="${2:-}"
  if [[ -z "$value" ]]; then echo "   - Skipping KV secret (no value): ${name}"; return; fi
  if [[ "$PROTECT_EXISTING" == "1" ]]; then
    local cur; cur="$(kv_get "$name")"
    if [[ -n "$cur" && "$cur" != "$value" ]]; then
      echo "   - Skipping overwrite for ${name} (exists). Set PROTECT_EXISTING=0 to force."
      return
    fi
  fi
  echo "   - Setting KV secret: ${name}"
  az keyvault secret set --vault-name "$KV" --name "$name" --value "$value" >/dev/null
}

echo "== Creating/updating Key Vault secrets from backend/.env"
kv_put "secret-key"            "${BACK_SECRET_KEY:-}"
kv_put "database-url"          "${BACK_DATABASE_URL:-${BACK_DATABASE_URL_NESTED:-}}"
kv_put "redis-url"             "${BACK_REDIS_URL:-}"
kv_put "openai-api-key"        "${BACK_OPENAI_API_KEY:-}"
kv_put "fal-ai-key"            "${BACK_FAL_AI_KEY:-}"
kv_put "groq-api-key"          "${BACK_GROQ_API_KEY:-}"
kv_put "turnstile-secret-key"  "${BACK_TURNSTILE_SECRET_KEY:-}"

# Register secret refs on the Container App
az containerapp secret set -g "$RG" -n "$APP" --secrets \
  secret-key=keyvaultref:"$KV_URI/secrets/secret-key",identityref:system \
  database-url=keyvaultref:"$KV_URI/secrets/database-url",identityref:system \
  redis-url=keyvaultref:"$KV_URI/secrets/redis-url",identityref:system \
  openai-api-key=keyvaultref:"$KV_URI/secrets/openai-api-key",identityref:system \
  fal-ai-key=keyvaultref:"$KV_URI/secrets/fal-ai-key",identityref:system \
  groq-api-key=keyvaultref:"$KV_URI/secrets/groq-api-key",identityref:system \
  turnstile-secret-key=keyvaultref:"$KV_URI/secrets/turnstile-secret-key",identityref:system >/dev/null || true

# ENABLE_TURNSTILE defaulting
ENABLE_TS="${BACK_ENABLE_TURNSTILE:-}"
if [[ -z "$ENABLE_TS" ]]; then
  if [[ "${APP_ENV_NORM}" == "local" ]]; then ENABLE_TS="false"; else ENABLE_TS="true"; fi
fi

# Apply env (creates a new revision in Single mode)
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

echo "== bind-kv-dev: done"
