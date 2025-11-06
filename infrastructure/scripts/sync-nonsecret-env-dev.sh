#!/usr/bin/env bash
# infrastructure/scripts/sync-nonsecret-env-dev.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_ENV="${BACKEND_ENV:-${REPO_ROOT}/backend/.env}"

RG="${RG:-rg-quizzical-shared}"
APP="${APP:-api-quizzical-dev}"
SUB="${SUB:-$(az account show --query id -o tsv)}"
APP_ENV="${APP_ENV:-azure}"
RESTART="${RESTART:-0}"

LOG_PROFILE="${LOG_PROFILE:-perf}"
LOG_LEVEL_ROOT="${LOG_LEVEL_ROOT:-INFO}"
LOG_LEVEL_APP="${LOG_LEVEL_APP:-INFO}"
LOG_LEVEL_LIBS="${LOG_LEVEL_LIBS:-WARNING}"

CONFIG_BUMP="${CONFIG_BUMP:-}"  # optional: force new revision when only non-secrets changed

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

BACK_ALLOWED_ORIGINS_LOCAL="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS")"
BACK_ALLOWED_ORIGINS_AZURE_DEV="$(get_env_val "$BACKEND_ENV" "ALLOWED_ORIGINS__AZURE_DEV")"
BACK_ENABLE_TURNSTILE="$(get_env_val "$BACKEND_ENV" "ENABLE_TURNSTILE")"
BACK_PROJECT_NAME="$(get_env_val "$BACKEND_ENV" "PROJECT__NAME")"
BACK_API_PREFIX="$(get_env_val "$BACKEND_ENV" "PROJECT__API_PREFIX")"
BACK_FRONTEND_APPNAME="$(get_env_val "$BACKEND_ENV" "FRONTEND__CONTENT__APPNAME")"
EMBED_MODEL_NAME="$(get_env_val "$BACKEND_ENV" "EMBEDDING__MODEL_NAME")"
EMBED_DIM="$(get_env_val "$BACKEND_ENV" "EMBEDDING__DIM")"
EMBED_DIST="$(get_env_val "$BACKEND_ENV" "EMBEDDING__DISTANCE_METRIC")"
EMBED_COL="$(get_env_val "$BACKEND_ENV" "EMBEDDING__COLUMN")"

ALLOWED_OVERRIDE="${ALLOWED_ORIGINS:-}"
APP_ENV_NORM="$(echo "${APP_ENV}" | tr '[:upper:]' '[:lower:]')"
DEFAULT_ALLOWED_LOCAL='["http://localhost:5173","http://127.0.0.1:5173","http://localhost:3000","http://127.0.0.1:3000"]'

if [[ -n "${ALLOWED_OVERRIDE}" ]]; then
  ALLOWED_EFFECTIVE="${ALLOWED_OVERRIDE}"
elif [[ "${APP_ENV_NORM}" != "local" && -n "${BACK_ALLOWED_ORIGINS_AZURE_DEV}" ]]; then
  ALLOWED_EFFECTIVE="${BACK_ALLOWED_ORIGINS_AZURE_DEV}"
else
  if [[ "${APP_ENV_NORM}" != "local" ]]; then
    echo "!! No ALLOWED_ORIGINS__AZURE_DEV found; skipping ALLOWED_ORIGINS in Azure to avoid overwriting."
    ALLOWED_EFFECTIVE=""
  else
    ALLOWED_EFFECTIVE="${BACK_ALLOWED_ORIGINS_LOCAL:-$DEFAULT_ALLOWED_LOCAL}"
  fi
fi

# Turnstile: default DISABLED for dev unless explicitly forced on
if [[ -z "${BACK_ENABLE_TURNSTILE:-}" ]]; then
  ENABLE_TS="false"
else
  case "$(echo "${BACK_ENABLE_TURNSTILE}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)  ENABLE_TS="true" ;;
    0|false|no|off) ENABLE_TS="false" ;;
    *)              ENABLE_TS="false" ;;
  esac
fi

API_PREFIX="${BACK_API_PREFIX:-/api/v1}"

declare -a PAIRS
PAIRS+=("APP_ENVIRONMENT=${APP_ENV}")
PAIRS+=("PROJECT__API_PREFIX=${API_PREFIX}")
PAIRS+=("LOG_PROFILE=${LOG_PROFILE}")
PAIRS+=("LOG_LEVEL_ROOT=${LOG_LEVEL_ROOT}")
PAIRS+=("LOG_LEVEL_APP=${LOG_LEVEL_APP}")
PAIRS+=("LOG_LEVEL_LIBS=${LOG_LEVEL_LIBS}")
PAIRS+=("ENABLE_TURNSTILE=${ENABLE_TS}")
[[ -n "${CONFIG_BUMP:-}" ]] && PAIRS+=("CONFIG_BUMP=${CONFIG_BUMP}")
[[ -n "${BACK_PROJECT_NAME:-}" ]]      && PAIRS+=("PROJECT__NAME=${BACK_PROJECT_NAME}")
[[ -n "${BACK_FRONTEND_APPNAME:-}" ]]  && PAIRS+=("FRONTEND__CONTENT__APPNAME=${BACK_FRONTEND_APPNAME}")
[[ -n "${EMBED_MODEL_NAME:-}" ]]       && PAIRS+=("EMBEDDING__MODEL_NAME=${EMBED_MODEL_NAME}")
[[ -n "${EMBED_DIM:-}" ]]              && PAIRS+=("EMBEDDING__DIM=${EMBED_DIM}")
[[ -n "${EMBED_DIST:-}" ]]             && PAIRS+=("EMBEDDING__DISTANCE_METRIC=${EMBED_DIST}")
[[ -n "${EMBED_COL:-}" ]]              && PAIRS+=("EMBEDDING__COLUMN=${EMBED_COL}")
[[ -n "${ALLOWED_EFFECTIVE:-}" ]]      && PAIRS+=("ALLOWED_ORIGINS=${ALLOWED_EFFECTIVE}")

az containerapp update -g "$RG" -n "$APP" --set-env-vars "${PAIRS[@]}" >/dev/null
echo "== non-secret env submitted"

if [[ "${RESTART}" == "1" ]]; then
  REV="$(az containerapp show -g "$RG" -n "$APP" --query "properties.latestReadyRevisionName" -o tsv)"
  if [[ -n "${REV}" ]]; then
    az containerapp revision restart -g "$RG" -n "$APP" --revision "$REV" >/dev/null
  fi
fi
