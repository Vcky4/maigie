#!/usr/bin/env bash

# Extract all environment variable keys from .env.example
# Usage: ./extract-env-keys.sh [.env.example path]

set -euo pipefail

TEMPLATE_FILE="${1:-.env.example}"

if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "Template file '$TEMPLATE_FILE' not found" >&2
  exit 1
fi

# Extract all variable names (keys) from KEY=VALUE lines
grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$TEMPLATE_FILE" | cut -d= -f1 | sort

