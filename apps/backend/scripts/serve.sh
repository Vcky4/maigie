#!/bin/bash
# Bash script to run the FastAPI server
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# Use virtual environment Python if available, otherwise try poetry
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
elif command -v poetry &> /dev/null; then
    poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
else
    echo "Error: Neither virtual environment nor Poetry found"
    exit 1
}

