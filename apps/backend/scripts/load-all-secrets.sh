#!/usr/bin/env bash

# This script reads .env.example and exports all environment variables
# that correspond to GitHub secrets. It's used in GitHub Actions workflows
# to dynamically load secrets based on keys found in .env.example
# Usage: source ./load-all-secrets.sh [.env.example path]

set -euo pipefail

TEMPLATE_FILE="${1:-.env.example}"

if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "Template file '$TEMPLATE_FILE' not found" >&2
  exit 1
fi

echo "Loading secrets from GitHub based on keys in '$TEMPLATE_FILE'..."

# Read .env.example and check for corresponding environment variables (from GitHub secrets)
while IFS= read -r line || [ -n "$line" ]; do
  # Skip empty lines and comments
  if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
    continue
  fi

  # Extract KEY from KEY=VALUE lines
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    
    # Check if this key exists as an environment variable (passed from GitHub secrets)
    # Note: In GitHub Actions, if a secret doesn't exist, it will be set to empty string
    # So we check if it's non-empty to determine if the secret was provided
    if [ -n "${!key:-}" ]; then
      echo "  ✓ Secret found for: ${key}"
      # Variable is already exported from GitHub Actions, no action needed
    else
      echo "  → No secret for: ${key} (will use default from .env.example)"
    fi
  fi
done < "$TEMPLATE_FILE"

echo "Done checking secrets."

