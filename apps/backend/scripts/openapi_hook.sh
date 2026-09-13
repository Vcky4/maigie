#!/usr/bin/env bash
# Regenerate the committed OpenAPI schema, for the pre-commit hook.
#
# CI runs `poetry run python scripts/export_openapi.py --check` and fails the build when the schema on
# disk no longer matches the app. That check is correct but it fires late: the contract had already
# been pushed by the time anyone saw it. This runs the same export at commit time.
#
# **It regenerates rather than only checking**, matching the ruff hooks above it: they auto-fix and
# still fail the commit (`--exit-non-zero-on-fix`) so the rewritten code gets read before it lands
# rather than after. Same reasoning here — a stale schema is mechanical to fix and there is no reason
# to make someone run a second command to fix it, but the diff is a change to a published contract and
# should be looked at, so pre-commit's "files were modified by this hook" failure is the right outcome.
#
# **Interpreter resolution.** CI has poetry; this repo's developers may have poetry, an activated
# virtualenv, or just `apps/backend/.venv`. A hook that hard-codes one of those silently stops running
# for everyone else, which is worse than not having the hook — so all three are tried, and if none can
# be found the hook fails loudly rather than passing on the assumption there was nothing to check.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PY=("${VIRTUAL_ENV}/bin/python")
elif [[ -x .venv/bin/python ]]; then
  PY=(.venv/bin/python)
elif command -v poetry >/dev/null 2>&1; then
  PY=(poetry run python)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
else
  echo "openapi hook: no Python interpreter found (tried \$VIRTUAL_ENV, .venv, poetry, python3)." >&2
  exit 1
fi

if ! "${PY[@]}" scripts/export_openapi.py >/dev/null; then
  # The app failed to build. That is a real problem the commit should not paper over, and the export
  # script's own stderr already says what broke.
  echo "openapi hook: could not build the schema — see the error above." >&2
  exit 1
fi

# `--check` after writing tells us whether the write changed anything, without diffing here and
# without assuming this file is the only thing in the index.
if ! git diff --quiet -- openapi.json; then
  cat >&2 <<'MSG'

openapi.json was out of date and has been regenerated.

The API contract changed. Read the diff, then stage it with the code that changed it:

    git add apps/backend/openapi.json

MSG
  exit 1
fi
