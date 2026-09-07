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

    # --- Normalize multi-line values so docker-compose `env_file` can parse them ---
    # docker-compose's env_file parser requires every physical line to be KEY=VALUE;
    # a raw multi-line secret (e.g. a pretty-printed service-account JSON or a PEM)
    # makes its continuation lines look like bare variable names and aborts the whole
    # file (observed: `unexpected character """ in variable name`). We fold such values
    # onto a single line here, choosing the transform by value shape:
    #   * JSON (starts with { or [): strip real newlines. Pretty-printed JSON only has
    #     insignificant whitespace between tokens, and any newline INSIDE a string is
    #     already an escaped \n, so this is lossless and keeps json.loads happy.
    #   * anything else (e.g. a raw PEM): escape real newlines to a literal \n so the
    #     value survives as one line; consumers that need the newlines un-escape them.
    if [[ "$value" == *$'\n'* ]]; then
      leading_ws="${value%%[![:space:]]*}"
      trimmed="${value#"$leading_ws"}"
      first_char="${trimmed:0:1}"
      if [[ "$first_char" == "{" || "$first_char" == "[" ]]; then
        value="$(printf '%s' "$value" | tr -d '\r\n')"
        echo "  ↳ collapsed multi-line JSON onto one line for ${key}"
      else
        value="${value//$'\r'/}"
        value="${value//$'\n'/\\n}"
        echo "  ↳ escaped newlines to literal \\n for ${key}"
      fi
    fi

    printf '%s=%s\n' "$key" "$value" >> "$OUTPUT_FILE"
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


