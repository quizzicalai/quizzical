#!/usr/bin/env bash
# infrastructure/scripts/sync-nonsecret-env-dev.sh
# Sync non-secret environment variables to Azure Container Apps (create/update, idempotent)

set -euo pipefail

# ------------------------------
# Paths
# ------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_ENV="${BACKEND_ENV:-${REPO_ROOT}/backend/.env}"

echo "== Script dir: ${SCRIPT_DIR}"
echo "== Repo root : ${REPO_ROOT}"
echo "== backend/.env : ${BACKEND_ENV} $( [[ -f "${BACKEND_ENV}" ]] && echo '(found)' || echo '(missing)' )"

# ------------------------------
# Azure Targets (override via env)
# ------------------------------
RG="${RG:-rg-quizzical-shared}"
APP="${APP:-api-quizzical-dev}"
SUB="${SUB:-$(az account show --query id -o tsv)}"

# FastAPI app environment on ACA (e.g., 'azure' for dev in Azure)
APP_ENV="${APP_ENV:-azure}"

# Optional revision restart after update (0/1)
RESTART="${RESTART:-0}"

# Fallback logging defaults
LOG_PROFILE="${LOG_PROFILE:-perf}"
LOG_LEVEL_ROOT="${LOG_LEVEL_ROOT:-INFO}"
LOG_LEVEL_APP="${LOG_LEVEL_APP:-INFO}"
LOG_LEVEL_LIBS="${LOG_LEVEL_LIBS:-WARNING}"

echo "== Subscription: ${SUB}"
az account set -s "$SUB"

# ------------------------------
# .env reader (no export)
# ------------------------------
get_env_val() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || { echo ""; return; }
  # last assignment wins; strip single/double quotes
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n1 | sed -E 's/^[[:space:]]*'"${key}"'[[:space:]]*=[[:space:]]*//')"
  line="${line%\"}" ; line="${line#\"}"
  line="${line%\'}" ; line="${line#\'}"
  echo "$line"
}

# ------------------------------
# Read non-secret values from backend/.env
# ------------------------------
# CORS
BACK_ALLOWED_ORIGINS_LOCAL="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS")"
BACK_ALLOWED_ORIGINS_AZURE_DEV="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS__AZURE_DEV")"

# Feature flags
BACK_ENABLE_TURNSTILE="$(get_env_val "$BACKEND_ENV" "ENABLE_TURNSTILE")"

# Project / runtime
BACK_PROJECT_NAME="$(get_env_val "$BACKEND_ENV" "PROJECT__NAME")"
BACK_API_PREFIX="$(get_env_val "$BACKEND_ENV" "PROJECT__API_PREFIX")"
BACK_FRONTEND_APPNAME="$(get_env_val "$BACKEND_ENV" "FRONTEND__CONTENT__APPNAME")"

# Embedding (non-secret)
EMBED_MODEL_NAME="$(get_env_val "$BACKEND_ENV" "EMBEDDING__MODEL_NAME")"
EMBED_DIM="$(get_env_val "$BACKEND_ENV" "EMBEDDING__DIM")"
EMBED_DIST="$(get_env_val "$BACKEND_ENV" "EMBEDDING__DISTANCE_METRIC")"
EMBED_COL="$(get_env_val "$BACKEND_ENV" "EMBEDDING__COLUMN")"

# ------------------------------
# Decide effective ALLOWED_ORIGINS (Azure dev wins, unless overridden)
# ------------------------------
# Allow shell override: ALLOWED_ORIGINS='["https://..."]' ./sync-nonsecret-env-dev.sh
ALLOWED_OVERRIDE="${ALLOWED_ORIGINS:-}"
APP_ENV_NORM="$(echo "${APP_ENV}" | tr '[:upper:]' '[:lower:]')"
DEFAULT_ALLOWED_LOCAL='["http://localhost:5173","http://127.0.0.1:5173","http://localhost:3000","http://127.0.0.1:3000"]'

if [[ -n "${ALLOWED_OVERRIDE}" ]]; then
  ALLOWED_EFFECTIVE="${ALLOWED_OVERRIDE}"
elif [[ "${APP_ENV_NORM}" != "local" && -n "${BACK_ALLOWED_ORIGINS_AZURE_DEV}" ]]; then
  ALLOWED_EFFECTIVE="${BACK_ALLOWED_ORIGINS_AZURE_DEV}"
else
  # IMPORTANT: avoid accidentally pushing local CORS to Azure if AZURE_DEV is not set.
  if [[ "${APP_ENV_NORM}" != "local" ]]; then
    echo "!! No ALLOWED_ORIGINS__AZURE_DEV found; skipping ALLOWED_ORIGINS in Azure to avoid overwriting."
    ALLOWED_EFFECTIVE=""
  else
    ALLOWED_EFFECTIVE="${BACK_ALLOWED_ORIGINS_LOCAL:-$DEFAULT_ALLOWED_LOCAL}"
  fi
fi

# ENABLE_TURNSTILE defaulting behavior:
# - If .env defines it, use that
# - Otherwise default to true when APP_ENV != local; false for local
if [[ -z "${BACK_ENABLE_TURNSTILE:-}" ]]; then
  if [[ "${APP_ENV_NORM}" == "local" ]]; then ENABLE_TS="false"; else ENABLE_TS="true"; fi
else
  # normalize to 'true'/'false'
  case "$(echo "${BACK_ENABLE_TURNSTILE}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)  ENABLE_TS="true" ;;
    0|false|no|off) ENABLE_TS="false" ;;
    *)              ENABLE_TS="true" ;;  # safest default in Azure
  esac
fi

# API prefix default
API_PREFIX="${BACK_API_PREFIX:-/api/v1}"

# ------------------------------
# Build the env var set (only non-secrets)
# ------------------------------
declare -a PAIRS
PAIRS+=("APP_ENVIRONMENT=${APP_ENV}")
PAIRS+=("PROJECT__API_PREFIX=${API_PREFIX}")
PAIRS+=("LOG_PROFILE=${LOG_PROFILE}")
PAIRS+=("LOG_LEVEL_ROOT=${LOG_LEVEL_ROOT}")
PAIRS+=("LOG_LEVEL_APP=${LOG_LEVEL_APP}")
PAIRS+=("LOG_LEVEL_LIBS=${LOG_LEVEL_LIBS}")
PAIRS+=("ENABLE_TURNSTILE=${ENABLE_TS}")

# Optional non-secrets (only set if present)
[[ -n "${BACK_PROJECT_NAME:-}" ]]      && PAIRS+=("PROJECT__NAME=${BACK_PROJECT_NAME}")
[[ -n "${BACK_FRONTEND_APPNAME:-}" ]]  && PAIRS+=("FRONTEND__CONTENT__APPNAME=${BACK_FRONTEND_APPNAME}")
[[ -n "${EMBED_MODEL_NAME:-}" ]]       && PAIRS+=("EMBEDDING__MODEL_NAME=${EMBED_MODEL_NAME}")
[[ -n "${EMBED_DIM:-}" ]]              && PAIRS+=("EMBEDDING__DIM=${EMBED_DIM}")
[[ -n "${EMBED_DIST:-}" ]]             && PAIRS+=("EMBEDDING__DISTANCE_METRIC=${EMBED_DIST}")
[[ -n "${EMBED_COL:-}" ]]              && PAIRS+=("EMBEDDING__COLUMN=${EMBED_COL}")
[[ -n "${ALLOWED_EFFECTIVE:-}" ]]      && PAIRS+=("ALLOWED_ORIGINS=${ALLOWED_EFFECTIVE}")

echo "== Setting non-secret env vars on Container App (create/update, idempotent)"
# Show what we’re about to set (redact nothing; these are non-secrets)
for kv in "${PAIRS[@]}"; do echo "   - ${kv}"; done

# ------------------------------
# Apply to Container App
# ------------------------------
# NOTE: This creates a new revision in Single mode and makes it active.
az containerapp update -g "$RG" -n "$APP" --set-env-vars "${PAIRS[@]}" >/dev/null

echo "== Non-secret env update submitted."

# ------------------------------
# Optional: restart the latest ready revision
# (normally NOT required for non-secrets, but available if you want a clean recycle)
# ------------------------------
if [[ "${RESTART}" == "1" ]]; then
  REV="$(az containerapp show -g "$RG" -n "$APP" --query "properties.latestReadyRevisionName" -o tsv)"
  if [[ -n "${REV}" ]]; then
    echo "== Restarting revision: ${REV}"
    az containerapp revision restart -g "$RG" -n "$APP" --revision "$REV" >/dev/null
  else
    echo "!! Could not resolve latestReadyRevisionName; skipping restart."
  fi
fi

# ------------------------------
# Verify
# ------------------------------
FQDN="$(az containerapp show -g "$RG" -n "$APP" --query "properties.configuration.ingress.fqdn" -o tsv)"
echo "== Done."
echo "Verify in portal: Container App → Revisions → latest → Template → Containers → Environment variables"
echo "CLI quick check:"
echo "  az containerapp show -g ${RG} -n ${APP} --query \"properties.template.containers[0].env\""
if [[ -n "${FQDN}" ]]; then
  echo "  curl -sS -D - \"https://${FQDN}/config\" -H 'Origin: https://kind-smoke-0ca2ff21e.3.azurestaticapps.net' -o /dev/null"
fi
