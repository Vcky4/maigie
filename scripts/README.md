# VPS Deployment Scripts

This directory contains scripts for managing preview environments on the Contabo VPS.

## Scripts

### `vps-setup.sh`
Initial setup script for the VPS. Run this once to:
- Create directory structure
- Install Docker and Docker Compose
- Set up cron job for automatic cleanup
- Copy utility scripts to VPS

**Usage:**
```bash
bash scripts/vps-setup.sh
```

### `cleanup-previews.sh`
Cleanup script that runs automatically via cron job. It:
- Cleans up previews for closed/merged PRs immediately
- Cleans up previews older than 3 days (if PR is still open)
- Removes Nginx configs for previews
- Prunes unused Docker resources

**Usage:**
```bash
# Without GitHub API (age-based cleanup only)
bash scripts/cleanup-previews.sh

# With GitHub API (checks PR status)
bash scripts/cleanup-previews.sh "GITHUB_TOKEN" "repo-owner" "repo-name"
```

**Cron Configuration:**
```bash
# Runs daily at 2 AM
# With Cloudflare API credentials (optional, for tunnel route cleanup)
0 2 * * * CLOUDFLARE_ACCOUNT_ID="your-account-id" CLOUDFLARE_TUNNEL_ID="your-tunnel-id" CLOUDFLARE_API_TOKEN="your-token" PREVIEW_DOMAIN="maigie.com" /opt/maigie/scripts/cleanup-previews.sh "GITHUB_TOKEN" "repo-owner" "repo-name" >> /var/log/maigie-cleanup.log 2>&1

# Without Cloudflare API credentials (tunnel routes must be cleaned manually)
0 2 * * * /opt/maigie/scripts/cleanup-previews.sh "GITHUB_TOKEN" "repo-owner" "repo-name" >> /var/log/maigie-cleanup.log 2>&1
```

### `setup-cloudflare-tunnel.sh`
Sets up Cloudflare Tunnel on the VPS for secure, SSL-enabled access to all environments.

**Usage:**
```bash
# With tunnel token
bash scripts/setup-cloudflare-tunnel.sh "YOUR_TUNNEL_TOKEN"

# Without token (manual setup)
bash scripts/setup-cloudflare-tunnel.sh
```

**What it does:**
- Installs `cloudflared` binary
- Creates systemd service
- Configures tunnel (if token provided)
- Enables and starts tunnel service

### `setup-nginx-routing.sh`
Configures Nginx routing for production, staging, and preview environments. Works with Cloudflare Tunnel.

**Usage:**
```bash
bash scripts/setup-nginx-routing.sh [PRODUCTION_DOMAIN] [STAGING_DOMAIN]

# Example:
bash scripts/setup-nginx-routing.sh api.maigie.com staging-api.maigie.com
```

**What it does:**
- Creates Nginx config for production API (default: `api.maigie.com` → `localhost:8000`)
- Creates Nginx config for staging API (default: `staging-api.maigie.com` → `localhost:8001`)
- Tests Nginx configuration
- Reloads Nginx

**Note:** Preview Nginx configs are created automatically by GitHub Actions workflow.

### `cloudflare-tunnel-routes.sh`
Manages Cloudflare Tunnel routes dynamically via Cloudflare API. Used by GitHub Actions workflow to create/remove preview routes.

**Usage:**
```bash
# Add a route
bash scripts/cloudflare-tunnel-routes.sh add pr-44.preview.maigie.com http://localhost:80

# Remove a route
bash scripts/cloudflare-tunnel-routes.sh remove pr-44.preview.maigie.com

# List all routes
bash scripts/cloudflare-tunnel-routes.sh list
```

**Required Environment Variables:**
- `CLOUDFLARE_ACCOUNT_ID` - Your Cloudflare Account ID
- `CLOUDFLARE_TUNNEL_ID` - Your Tunnel ID
- `CLOUDFLARE_API_TOKEN` - API token with Tunnel:Edit permission

**Note:** This script is primarily used by GitHub Actions workflow. Manual usage is optional.

## Cleanup Logic

1. **On PR Close/Merge**: GitHub Actions workflow (`cleanup-preview` job) automatically cleans up immediately
2. **Daily Cron Job**: Checks all previews and cleans up:
   - Previews for closed/merged PRs (regardless of age)
   - Previews older than 3 days (if PR is still open)

## GitHub Token Setup (Optional)

To enable PR status checking in the cleanup script:

1. Create a GitHub Personal Access Token with `public_repo` scope
2. Store it securely on the VPS (e.g., in a file with restricted permissions)
3. Update the cron job to pass the token

```bash
# Create token file
echo "your_github_token" > /root/.github_token
chmod 600 /root/.github_token

# Update cron job
crontab -e
# Change to:
0 2 * * * /opt/maigie/scripts/cleanup-previews.sh "$(cat /root/.github_token)" "your-org" "your-repo" >> /var/log/maigie-cleanup.log 2>&1
```

## Logs

Cleanup logs are written to `/var/log/maigie-cleanup.log`. Check this file to monitor cleanup activity:

```bash
tail -f /var/log/maigie-cleanup.log
```

