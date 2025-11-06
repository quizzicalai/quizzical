#!/usr/bin/env bash
# infrastructure/scripts/sync-config.sh
# =============================================================================
# Sync Configuration to Azure App Configuration
# =============================================================================
set -euo pipefail

PROJECT_NAME="quizzical"
CONFIG_FILE_PATH="${CONFIG_FILE_PATH:-../config-data/base-config.yml}"

if [[ $# -lt 1 ]]; then
  echo "❌ Error: Environment not specified."
  echo "Usage: $0 <local|stage|prod>"
  exit 1
fi

ENVIRONMENT="$1"
APP_CONFIG_STORE_NAME="${PROJECT_NAME}-shared-appcs"

echo "Verifying Azure login status..."
if ! az account show > /dev/null 2>&1; then
  echo "You are not logged into Azure. Please run 'az login' first."
  exit 1
fi
echo "✅ Azure login verified."

echo "🚀 Importing config:"
echo "   - Store: $APP_CONFIG_STORE_NAME"
echo "   - File:  $CONFIG_FILE_PATH"
echo "   - Label: $ENVIRONMENT"

# Import from YAML; flatten with ':' to produce hierarchical keys
az appconfig kv import \
  --name "$APP_CONFIG_STORE_NAME" \
  --source file \
  --path "$CONFIG_FILE_PATH" \
  --format yaml \
  --label "$ENVIRONMENT" \
  --separator ":" \
  --yes

echo "✅ App Configuration import complete for label '$ENVIRONMENT'."
