#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.11+ and re-run." >&2
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY
then
  :
else
  echo "Python 3.11+ is required by apps/backend/pyproject.toml. Found ${PY_VER}." >&2
  echo "Install with Homebrew: brew install python@3.11" >&2
  exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
  if command -v pipx >/dev/null 2>&1; then
    pipx install poetry
  else
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$HOME/.local/bin:$HOME/Library/Python/${PY_VER}/bin:$PATH"
    pipx install poetry
  fi
fi

poetry config virtualenvs.in-project true
poetry install --no-root

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

poetry run python3 verify_setup.py || true

echo "Setup complete. Start with:"
echo "  poetry run uvicorn src.main:app --reload"
