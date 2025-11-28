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
# Cloudflare Tunnel setup script for VPS
# Usage: bash scripts/setup-cloudflare-tunnel.sh [TUNNEL_TOKEN]

set -e

TUNNEL_TOKEN="${1:-}"
CLOUDFLARED_VERSION="2024.11.0"
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/root/.cloudflared"
SERVICE_NAME="cloudflared"

echo "🚀 Setting up Cloudflare Tunnel..."

# Check if cloudflared is already installed
if command -v cloudflared &> /dev/null; then
    echo "✓ cloudflared is already installed"
    cloudflared --version
else
    echo "📥 Installing cloudflared..."
    curl -L "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64" -o "${INSTALL_DIR}/cloudflared"
    chmod +x "${INSTALL_DIR}/cloudflared"
    echo "✓ cloudflared installed successfully"
fi

# Create config directory
mkdir -p "${CONFIG_DIR}"

# If tunnel token provided, set it up
if [ -n "$TUNNEL_TOKEN" ]; then
    echo "🔐 Configuring tunnel with provided token..."
    echo "$TUNNEL_TOKEN" > "${CONFIG_DIR}/tunnel_token"
    cloudflared tunnel run --token "$TUNNEL_TOKEN" &
    echo "✓ Tunnel started with provided token"
else
    echo "⚠️  No tunnel token provided. You'll need to:"
    echo "   1. Create a tunnel in Cloudflare Zero Trust Dashboard"
    echo "   2. Copy the tunnel token"
    echo "   3. Run: cloudflared tunnel login"
    echo "   4. Create tunnel: cloudflared tunnel create maigie-backend-tunnel"
    echo "   5. Configure tunnel config file at ${CONFIG_DIR}/config.yml"
fi

# Create systemd service file
echo "📝 Creating systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=simple
User=root
ExecStart=${INSTALL_DIR}/cloudflared tunnel run
Restart=on-failure
RestartSec=5s
Environment="TUNNEL_TOKEN_FILE=${CONFIG_DIR}/tunnel_token"

[Install]
WantedBy=multi-user.target
EOF

# If using config file instead of token
if [ -f "${CONFIG_DIR}/config.yml" ]; then
    # Update service to use config file
    sed -i 's|ExecStart=.*|ExecStart='"${INSTALL_DIR}"'/cloudflared tunnel run|' "/etc/systemd/system/${SERVICE_NAME}.service"
    sed -i '/Environment=/d' "/etc/systemd/system/${SERVICE_NAME}.service"
fi

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo ""
echo "✅ Cloudflare Tunnel setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Create tunnel in Cloudflare Zero Trust Dashboard"
echo "2. Copy tunnel token and save to: ${CONFIG_DIR}/tunnel_token"
echo "   OR create config file at: ${CONFIG_DIR}/config.yml"
echo ""
echo "3. Configure DNS in Cloudflare:"
echo "   - api.maigie.com → CNAME → {TUNNEL_ID}.cfargotunnel.com (Proxied)"
echo "   - staging-api.maigie.com → CNAME → {TUNNEL_ID}.cfargotunnel.com (Proxied)"
echo "   - *.preview.maigie.com → CNAME → {TUNNEL_ID}.cfargotunnel.com (Proxied)"
echo ""
echo "4. Start tunnel service:"
echo "   sudo systemctl start ${SERVICE_NAME}"
echo ""
echo "5. Check tunnel status:"
echo "   sudo systemctl status ${SERVICE_NAME}"

