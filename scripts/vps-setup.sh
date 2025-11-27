#!/bin/bash
# Copyright (C) 2025 Maigie Team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Run this once on your Contabo VPS to set up the directory structure
# Usage: bash scripts/vps-setup.sh

set -e

echo "🚀 Setting up Maigie VPS deployment structure..."

# Create directories
echo "📁 Creating directories..."
mkdir -p /opt/maigie/{production,staging,previews}
mkdir -p /opt/maigie/production
mkdir -p /opt/maigie/staging

# Install Docker if not already installed
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
else
    echo "✓ Docker already installed"
fi

# Install Docker Compose if not already installed
if ! command -v docker-compose &> /dev/null; then
    echo "🐳 Installing Docker Compose..."
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
else
    echo "✓ Docker Compose already installed"
fi

# Copy docker-compose.yml to production and staging directories
echo "📋 Setting up production environment..."
if [ -f "/opt/maigie/production/docker-compose.yml" ]; then
    echo "  Production docker-compose.yml already exists, skipping..."
else
    echo "  Creating production docker-compose.yml..."
    cat > /opt/maigie/production/docker-compose.yml << 'EOF'
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    container_name: maigie-redis-prod
    restart: unless-stopped
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    networks:
      - maigie_network

  celery-worker:
    build:
      context: /opt/maigie/production
      dockerfile: Dockerfile
    container_name: maigie-celery-worker-prod
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - redis
    networks:
      - maigie_network
    command: celery -A src.core.celery_app worker --loglevel=info

  celery-beat:
    build:
      context: /opt/maigie/production
      dockerfile: Dockerfile
    container_name: maigie-celery-beat-prod
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - redis
    networks:
      - maigie_network
    command: celery -A src.core.celery_app beat --loglevel=info

  backend:
    build:
      context: /opt/maigie/production
      dockerfile: Dockerfile
    container_name: maigie-backend-prod
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "8000:8000"
    depends_on:
      - redis
    networks:
      - maigie_network
    command: >
      sh -c "
        prisma migrate deploy &&
        uvicorn src.main:app --host 0.0.0.0 --port 8000
      "

volumes:
  redis_data:

networks:
  maigie_network:
    driver: bridge
EOF
fi

echo "📋 Setting up staging environment..."
if [ -f "/opt/maigie/staging/docker-compose.yml" ]; then
    echo "  Staging docker-compose.yml already exists, skipping..."
else
    echo "  Creating staging docker-compose.yml..."
    cp /opt/maigie/production/docker-compose.yml /opt/maigie/staging/docker-compose.yml
    # Update container names for staging
    sed -i 's/-prod/-staging/g' /opt/maigie/staging/docker-compose.yml
    sed -i 's/production/staging/g' /opt/maigie/staging/docker-compose.yml
    sed -i 's/8000:8000/8001:8000/g' /opt/maigie/staging/docker-compose.yml
fi

# Set up cleanup cron job
echo "⏰ Setting up preview cleanup cron job..."
# Note: Update REPO_OWNER and REPO_NAME with your GitHub repository details
# You can also add GITHUB_TOKEN as a secret on the VPS for PR status checking
CRON_JOB="0 2 * * * /opt/maigie/scripts/cleanup-previews.sh \"\" \"REPO_OWNER\" \"REPO_NAME\" >> /var/log/maigie-cleanup.log 2>&1"
(crontab -l 2>/dev/null | grep -v "cleanup-previews.sh"; echo "$CRON_JOB") | crontab -
echo "  ⚠️  Remember to update REPO_OWNER and REPO_NAME in the cron job!"

# Copy scripts to VPS
echo "📋 Copying scripts to /opt/maigie/scripts/..."
mkdir -p /opt/maigie/scripts
if [ -f "scripts/cleanup-previews.sh" ]; then
    cp scripts/cleanup-previews.sh /opt/maigie/scripts/
    chmod +x /opt/maigie/scripts/cleanup-previews.sh
fi
if [ -f "scripts/setup-cloudflare-tunnel.sh" ]; then
    cp scripts/setup-cloudflare-tunnel.sh /opt/maigie/scripts/
    chmod +x /opt/maigie/scripts/setup-cloudflare-tunnel.sh
fi
if [ -f "scripts/setup-nginx-routing.sh" ]; then
    cp scripts/setup-nginx-routing.sh /opt/maigie/scripts/
    chmod +x /opt/maigie/scripts/setup-nginx-routing.sh
fi

echo ""
echo "✅ VPS setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Set up Cloudflare Tunnel (optional but recommended):"
echo "   bash /opt/maigie/scripts/setup-cloudflare-tunnel.sh"
echo "   OR follow manual setup in DEPLOYMENT.md"
echo ""
echo "2. Set up Nginx routing:"
echo "   bash /opt/maigie/scripts/setup-nginx-routing.sh api.maigie.com staging-api.maigie.com"
echo ""
echo "3. Add GitHub secrets:"
echo "   - VPS_HOST: Your Contabo VPS IP or domain"
echo "   - VPS_USER: SSH username (usually 'root' or 'ubuntu')"
echo "   - VPS_SSH_KEY: Private SSH key for VPS access"
echo "   - PRODUCTION_DATABASE_URL: Your Neon/Supabase production DB URL"
echo "   - STAGING_DATABASE_URL: Your Neon/Supabase staging DB URL"
echo "   - PREVIEW_DOMAIN: Your domain for previews (e.g., 'maigie.com')"
echo ""
echo "4. Copy your Dockerfile to /opt/maigie/production/ and /opt/maigie/staging/"
echo ""
echo "5. Create .env files in production and staging directories with DATABASE_URL"
echo ""
echo "6. Push to main or development branch to trigger deployment"

