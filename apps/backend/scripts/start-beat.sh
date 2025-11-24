#!/bin/bash
# Script to start Celery Beat scheduler for Maigie backend
# Usage: ./scripts/start-beat.sh [options]

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# Default values
LOGLEVEL="info"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --loglevel)
            LOGLEVEL="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --loglevel LEVEL    Log level: debug, info, warning, error (default: info)"
            echo "  --help              Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Check if virtual environment exists
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif command -v poetry &> /dev/null; then
    echo "Using Poetry environment"
else
    echo "Error: No virtual environment found. Please run 'poetry install' first."
    exit 1
fi

# Start Celery Beat
echo "Starting Celery Beat scheduler..."
echo "Log level: $LOGLEVEL"
echo ""

celery -A src.core.celery_app:celery_app beat \
    --loglevel="$LOGLEVEL"

