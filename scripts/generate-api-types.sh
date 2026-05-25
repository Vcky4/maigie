#!/bin/bash
# Generate TypeScript types from the backend OpenAPI schema.
#
# Prerequisites:
#   npm install -g openapi-typescript
#
# Usage:
#   ./scripts/generate-api-types.sh [API_URL]
#
# The generated types can be shared across maigie-client and maigie-mobile
# to prevent API contract drift.

set -e

API_URL="${1:-http://localhost:8000}"
OUTPUT_DIR="libs/types/src/generated"

echo "Fetching OpenAPI schema from ${API_URL}/openapi.json..."

mkdir -p "$OUTPUT_DIR"

# Generate TypeScript types from OpenAPI schema
npx openapi-typescript "${API_URL}/openapi.json" \
  --output "${OUTPUT_DIR}/api-types.ts" \
  --export-type

echo "✅ TypeScript types generated at ${OUTPUT_DIR}/api-types.ts"
echo ""
echo "Copy to client repos:"
echo "  cp ${OUTPUT_DIR}/api-types.ts ../maigie-client/libs/types/src/generated/"
echo "  cp ${OUTPUT_DIR}/api-types.ts ../maigie-mobile/src/types/generated/"
