#!/bin/bash
# Bash script to run the FastAPI server
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# WeasyPrint (macOS) needs the Homebrew Pango/Cairo libs discoverable.
# On Linux, these libs are typically in a standard lib path already.
if [ "$(uname)" = "Darwin" ] && [ -d "/opt/homebrew/lib" ]; then
    export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:${DYLD_FALLBACK_LIBRARY_PATH}"
fi

# Use virtual environment Python if available, otherwise try poetry
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
elif command -v poetry &> /dev/null; then
    poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
else
    echo "Error: Neither virtual environment nor Poetry found"
    exit 1
fi
