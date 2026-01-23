#!/bin/bash
# Script to start Celery worker for Maigie backend
# Usage: ./scripts/start-worker.sh [options]

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# Default values
WORKER_NAME="worker@%h"
QUEUE="default"
CONCURRENCY="4"
LOGLEVEL="info"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            WORKER_NAME="$2"
            shift 2
            ;;
        --queue)
            QUEUE="$2"
            shift 2
            ;;
        --concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        --loglevel)
            LOGLEVEL="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --name NAME         Worker name (default: worker@%h)"
            echo "  --queue QUEUE       Queue name (default: default)"
            echo "  --concurrency N     Number of worker processes (default: 4)"
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

# Start Celery worker
echo "Starting Celery worker..."
echo "Worker name: $WORKER_NAME"
echo "Queue: $QUEUE"
echo "Concurrency: $CONCURRENCY"
echo "Log level: $LOGLEVEL"
echo ""

celery -A src.core.celery_app:celery_app worker \
    --loglevel="$LOGLEVEL" \
    --concurrency="$CONCURRENCY" \
    --hostname="$WORKER_NAME" \
    --queues="$QUEUE" \
    --without-gossip \
    --without-mingle \
    --without-heartbeat

