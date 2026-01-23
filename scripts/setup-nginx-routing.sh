#!/bin/bash
# Copyright (C) 2025 Maigie
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
# Nginx routing setup for production, staging, and preview environments
# This script creates Nginx configs that work with Cloudflare Tunnel
# Usage: bash scripts/setup-nginx-routing.sh [PRODUCTION_DOMAIN] [STAGING_DOMAIN]

set -e

PRODUCTION_DOMAIN="${1:-api.maigie.com}"
STAGING_DOMAIN="${2:-staging-api.maigie.com}"
NGINX_CONFIG_DIR="/www/server/panel/vhost/nginx"

echo "🔧 Setting up Nginx routing for Cloudflare Tunnel..."

# Production API config
echo "📝 Creating production API config..."
cat > /tmp/nginx-production.conf << EOF
server {
    listen 80;
    server_name ${PRODUCTION_DOMAIN};
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }
}
EOF

# Staging API config
echo "📝 Creating staging API config..."
cat > /tmp/nginx-staging.conf << EOF
server {
    listen 80;
    server_name ${STAGING_DOMAIN};
    
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:8001/health;
        access_log off;
    }
}
EOF

# Copy configs to Nginx directory
sudo cp /tmp/nginx-production.conf "${NGINX_CONFIG_DIR}/maigie-production.conf"
sudo cp /tmp/nginx-staging.conf "${NGINX_CONFIG_DIR}/maigie-staging.conf"

# Test and reload Nginx
echo "🧪 Testing Nginx configuration..."
sudo nginx -t

if [ $? -eq 0 ]; then
    echo "✅ Nginx configuration is valid"
    echo "🔄 Reloading Nginx..."
    sudo systemctl reload nginx
    echo "✅ Nginx reloaded successfully"
else
    echo "❌ Nginx configuration test failed"
    exit 1
fi

# Cleanup temp files
rm -f /tmp/nginx-production.conf /tmp/nginx-staging.conf

echo ""
echo "✅ Nginx routing setup complete!"
echo ""
echo "📋 Created configs:"
echo "   - Production: ${NGINX_CONFIG_DIR}/maigie-production.conf (${PRODUCTION_DOMAIN} → localhost:8000)"
echo "   - Staging: ${NGINX_CONFIG_DIR}/maigie-staging.conf (${STAGING_DOMAIN} → localhost:8001)"
echo ""
echo "📝 Preview configs will be created automatically by GitHub Actions workflow"

