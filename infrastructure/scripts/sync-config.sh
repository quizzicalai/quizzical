#!/bin/bash

# =============================================================================
# Sync Configuration to Azure App Configuration
# =============================================================================
# This script imports key-values from a YAML configuration file into a specific
# Azure App Configuration store. It uses a command-line argument to apply an
# environment-specific label (e.g., local, stage, prod) to the imported keys.
#
# This allows for managing multiple environments from a single source file,
# with overrides handled by Azure App Configuration.
#
# Usage:
#   ./sync-config.sh <environment>
#
# Example:
#   # Sync configuration for local development
#   ./sync-config.sh local
# =============================================================================

# --- Configuration ---
# Exit immediately if a command exits with a non-zero status.
set -e

# The base name of your project, used to construct the App Config store name.
PROJECT_NAME="quizzical"

# The relative path to the source configuration file.
CONFIG_FILE_PATH="../config-data/base-config.yml"


# --- Script Logic ---

# 1. Validate Input: Ensure an environment (e.g., 'local') was provided.
if [ -z "$1" ]; then
    echo "❌ Error: Environment not specified."
    echo "Usage: $0 <local|stage|prod>"
    exit 1
fi

ENVIRONMENT=$1
APP_CONFIG_STORE_NAME="${PROJECT_NAME}-shared-appcs"

# 2. Verify Azure CLI Login: Check for an active Azure session.
echo "Verifying Azure login status..."
if ! az account show > /dev/null 2>&1; then
    echo " MINGW64 /c/quizzicalai/quizzical (main)
$ You are not logged into Azure. Please run 'az login' first."
    exit 1
fi
echo "✅ Azure login verified."

# 3. Import Configuration: Execute the Azure CLI command.
echo "🚀 Starting import for environment: '$ENVIRONMENT'..."
echo "   - Target Store: $APP_CONFIG_STORE_NAME"
echo "   - Source File:  $CONFIG_FILE_PATH"

az appconfig kv import \
    --name "$APP_CONFIG_STORE_NAME" \
    --source file \
    --path "$CONFIG_FILE_PATH" \
    --format yaml \
    --label "$ENVIRONMENT" \
    --separator ":" \
    --yes  # Automatically confirm overwrite of existing keys

echo "✅ Successfully imported configuration with label '$ENVIRONMENT'."