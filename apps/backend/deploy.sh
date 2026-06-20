#!/bin/bash
# Zero-downtime deployment script for Maigie backend on Contabo VPS.
#
# Usage:
#   ./deploy.sh              # Full deploy (build + graceful restart)
#   ./deploy.sh --quick      # Quick restart (no rebuild)
#   ./deploy.sh --celery     # Only restart celery worker + beat
#
# This works with your existing Nginx reverse proxy that handles SSL.
# Downtime is minimized to ~2-3 seconds during the container swap.
#
# For TRUE zero-downtime (0 dropped requests), you'd need either:
#   - Traefik with replicas (see docker-compose.traefik.yml)
#   - Or Nginx upstream with multiple backends

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
PROJECT_DIR="/opt/maigie/production"

echo "═══════════════════════════════════════════════════════"
echo "  Maigie Backend — Deploy"
echo "═══════════════════════════════════════════════════════"
echo ""

ACTION="${1:-deploy}"

case "$ACTION" in
  --quick)
    echo "⚡ Quick restart (no rebuild)..."
    docker compose -f "$COMPOSE_FILE" restart backend
    ;;

  --celery)
    echo "🔄 Restarting Celery worker + beat..."
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps celery-worker celery-beat
    ;;

  *)
    echo "📦 Building new image..."
    docker compose -f "$COMPOSE_FILE" build --no-cache backend

    echo ""
    echo "🚀 Deploying backend (graceful restart)..."
    # Stop old container gracefully (30s grace period for in-flight requests)
    # Then start new container immediately
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend

    echo ""
    echo "🔄 Updating Celery worker & beat..."
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps celery-worker celery-beat
    ;;
esac

echo ""
echo "⏳ Waiting for health check..."
sleep 10

# Health check
for i in {1..6}; do
  HEALTH=$(curl -sf http://localhost:8000/health 2>/dev/null || echo "UNHEALTHY")
  if echo "$HEALTH" | grep -qi "ok\|healthy\|200"; then
    echo "   ✓ Backend is healthy!"
    break
  fi
  if [ $i -eq 6 ]; then
    echo "   ⚠ Health check failed after 60s. Check logs:"
    echo "     docker compose -f $COMPOSE_FILE logs --tail=30 backend"
    exit 1
  fi
  echo "   Attempt $i/6 — waiting..."
  sleep 10
done

echo ""
echo "📋 Container status:"
docker compose -f "$COMPOSE_FILE" ps
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅ Deploy complete"
echo "═══════════════════════════════════════════════════════"
