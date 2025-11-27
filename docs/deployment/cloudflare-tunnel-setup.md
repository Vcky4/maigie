# Cloudflare Tunnel Setup Guide

This guide explains how to set up Cloudflare Tunnel to expose all environments (production, staging, and previews) through a single tunnel with automatic SSL.

## Architecture

```
Internet → Cloudflare Tunnel → Nginx (port 80) → Docker Containers
                                    ↓
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
            Production:8000  Staging:8001  Preview:{PORT}
```

## Benefits

- ✅ **No Port Exposure**: All containers only listen on localhost
- ✅ **Automatic SSL**: HTTPS via Cloudflare (no certificate management)
- ✅ **DDoS Protection**: Built-in Cloudflare protection
- ✅ **Clean URLs**: Domain-based instead of IP:port
- ✅ **Firewall Friendly**: No need to open ports

## Setup Steps

### 1. Install Cloudflared on VPS

```bash
# Run the setup script
bash /opt/maigie/scripts/setup-cloudflare-tunnel.sh

# OR manually install
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
```

### 2. Create Tunnel in Cloudflare

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/)
2. Navigate to **Networks** → **Tunnels**
3. Click **Create a tunnel**
4. Choose **Cloudflared** connector
5. Name it: `maigie-backend-tunnel`
6. Copy the **Tunnel Token**

### 3. Configure Tunnel on VPS

**Option A: Using Token (Simpler)**

```bash
# Save token to file
echo "YOUR_TUNNEL_TOKEN" > /root/.cloudflared/tunnel_token

# Start tunnel service
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

**Option B: Using Config File**

```bash
# Authenticate
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create maigie-backend-tunnel

# Create config file
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << EOF
tunnel: {TUNNEL_UUID}
credentials-file: /root/.cloudflared/{TUNNEL_UUID}.json

ingress:
  - hostname: api.maigie.com
    service: http://localhost:80
  - hostname: staging-api.maigie.com
    service: http://localhost:80
  - service: http_status:404
EOF

**Note:** Preview routes (e.g., `pr-44-api-preview.maigie.com`) are added automatically via Cloudflare API when previews are deployed. No wildcard route is needed in the tunnel configuration.

# Start tunnel service
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

**Important:** If you're using token-based setup, configure ingress rules in the Cloudflare Dashboard:
1. Go to Zero Trust → Networks → Tunnels → your tunnel → Public Hostname
2. Add routes for production and staging only:
   - `api.maigie.com` → `http://localhost:80`
   - `staging-api.maigie.com` → `http://localhost:80`
3. Preview routes will be added automatically via API

### 4. Set Up Nginx Routing

```bash
# Run the Nginx setup script
bash /opt/maigie/scripts/setup-nginx-routing.sh api.maigie.com staging-api.maigie.com

# This creates:
# - /www/server/panel/vhost/nginx/maigie-production.conf (api.maigie.com → localhost:8000)
# - /www/server/panel/vhost/nginx/maigie-staging.conf (staging-api.maigie.com → localhost:8001)
```

### 5. Configure DNS in Cloudflare

Add these DNS records for production and staging (preview DNS records are created automatically via API):

```
Type: CNAME
Name: api
Target: {TUNNEL_ID}.cfargotunnel.com
Proxy: Proxied (orange cloud)

Type: CNAME
Name: staging-api
Target: {TUNNEL_ID}.cfargotunnel.com
Proxy: Proxied (orange cloud)
```

**Note:** Preview DNS records (e.g., `pr-44-api-preview.maigie.com`) are created automatically via Cloudflare API when previews are deployed. No wildcard DNS record is needed.

### 6. GitHub Secrets

Add these secrets to your GitHub repository:

- `PREVIEW_DOMAIN` - Your domain (e.g., `maigie.com`)

**Required for Dynamic Route Management** (recommended):
- `CLOUDFLARE_ACCOUNT_ID` - Your Cloudflare Account ID (found in Cloudflare Dashboard → Right sidebar)
- `CLOUDFLARE_TUNNEL_ID` - Your Tunnel ID (found in Zero Trust → Networks → Tunnels → your tunnel)
- `CLOUDFLARE_ZONE_ID` - Your Zone ID for maigie.com (found in Cloudflare Dashboard → Domain → Overview → API → Zone ID)
- `CLOUDFLARE_API_TOKEN` - API token with `Account.Cloudflare Tunnel:Edit` and `Zone.DNS:Edit` permissions

**To create API token:**
1. Go to Cloudflare Dashboard → My Profile → API Tokens
2. Click "Create Token"
3. Use "Edit Cloudflare Tunnel" template as a starting point
4. Add permissions:
   - `Account` → `Cloudflare Tunnel` → `Edit`
   - `Zone` → `DNS` → `Edit`
5. Add account resources: Select your account
6. Add zone resources: Include → All zones (or select maigie.com specifically)
7. Copy the token and add to GitHub Secrets

**Note:** Without these secrets, preview routes and DNS records will need to be managed manually in the Cloudflare Dashboard. With these secrets, routes and DNS records are automatically created and removed by GitHub Actions.

## How It Works

1. **All Traffic Routes Through Tunnel**:
   - `https://api.maigie.com` → Tunnel → Nginx → `localhost:8000` (Production)
   - `https://staging-api.maigie.com` → Tunnel → Nginx → `localhost:8001` (Staging)
   - `https://pr-44-api-preview.maigie.com` → Tunnel → Nginx → `localhost:{PORT}` (Preview)

2. **Preview Deployment**:
   - Docker container starts on random port
   - Workflow creates Nginx config: `pr-44-api-preview.maigie.com` → `localhost:PORT`
   - Workflow creates DNS record via API: `pr-44-api-preview` CNAME → `{tunnel-name}.cfargotunnel.com` (Proxied)
   - Workflow creates Cloudflare Tunnel route via API: `pr-44-api-preview.maigie.com` → `http://localhost:80`
   - Nginx reloads
   - Preview URL commented on PR

3. **Cleanup**:
   - Cloudflare Tunnel route removed via API
   - Nginx config removed
   - Docker containers stopped
   - Preview directory removed

## Troubleshooting

### Tunnel not connecting

```bash
# Check tunnel status
sudo systemctl status cloudflared

# Check logs
sudo journalctl -u cloudflared -f

# Verify token/config
cat /root/.cloudflared/tunnel_token
# OR
cat ~/.cloudflared/config.yml
```

### Routes not working

- Verify DNS records are configured correctly
- Check tunnel config has correct ingress rules
- Ensure Nginx configs exist and are valid
- Test Nginx: `sudo nginx -t`
- Check Nginx logs: `sudo tail -f /www/wwwlogs/nginx_error.log`

### Preview domains not resolving

- Verify DNS record was created via API (check Cloudflare Dashboard → DNS)
- Verify tunnel route was created via API (check Zero Trust → Networks → Tunnels → Public Hostname)
- Verify Nginx config was created for the preview
- Check Nginx is reloaded: `sudo systemctl reload nginx`

## Files Modified

- `.github/workflows/backend-ci.yml` - Added Nginx config creation for previews
- `scripts/cleanup-previews.sh` - Added Nginx config removal
- `scripts/setup-cloudflare-tunnel.sh` - New script for tunnel setup
- `scripts/setup-nginx-routing.sh` - New script for Nginx routing
- `scripts/vps-setup.sh` - Updated to copy new scripts
- `apps/backend/DEPLOYMENT.md` - Added Cloudflare Tunnel section

## Next Steps

1. Set up Cloudflare Tunnel on VPS
2. Configure DNS records
3. Set up Nginx routing
4. Add `PREVIEW_DOMAIN` to GitHub Secrets
5. Test with a PR to verify preview URLs work

