#!/usr/bin/env bash

set -euo pipefail

TEMPLATE_FILE="${2:-.env.example}"
OUTPUT_FILE="${1:-.env}"

if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "Template file '$TEMPLATE_FILE' not found"
  exit 1
fi

echo "=========================================="
echo "Generating '$OUTPUT_FILE' from '$TEMPLATE_FILE'"
echo "=========================================="

> "$OUTPUT_FILE"

SECRETS_USED=0
DEFAULTS_USED=0
TOTAL_VALUES=0

while IFS= read -r line || [ -n "$line" ]; do
  # Preserve empty lines and comments
  if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
    echo "$line" >> "$OUTPUT_FILE"
    continue
  fi

  # Handle KEY=VALUE lines
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    default_value="${BASH_REMATCH[2]}"
    TOTAL_VALUES=$((TOTAL_VALUES + 1))

    # Check if environment variable (GitHub secret) is set
    if [ -n "${!key:-}" ]; then
      # Use secret value
      value="${!key}"
      echo "✓ Using GitHub secret for ${key}"
      SECRETS_USED=$((SECRETS_USED + 1))
    else
      # Use default value from template
      value="$default_value"
      echo "→ Using default value for ${key}"
      DEFAULTS_USED=$((DEFAULTS_USED + 1))
    fi
    
    echo "${key}=${value}" >> "$OUTPUT_FILE"
  else
    # Any other line is copied as-is
    echo "$line" >> "$OUTPUT_FILE"
  fi
done < "$TEMPLATE_FILE"

echo "=========================================="
echo "Summary:"
echo "  Total values processed: ${TOTAL_VALUES}"
echo "  GitHub secrets used: ${SECRETS_USED}"
echo "  Default values used: ${DEFAULTS_USED}"
echo "=========================================="
echo "Generated '$OUTPUT_FILE' successfully."


