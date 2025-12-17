# Cleaning Up Orphaned Preview Containers

## Problem

Containers from closed PRs may still be running if:
1. The cleanup workflow didn't trigger when the PR was closed
2. The cleanup workflow failed
3. Containers were created before the cleanup workflow was added
4. The cleanup script isn't running via cron

## Solution

We've added multiple cleanup mechanisms:

### 1. Manual Cleanup Workflow (Recommended)

A GitHub Actions workflow that can be manually triggered to clean up all orphaned containers:

**How to use:**
1. Go to the Actions tab in GitHub
2. Select "Cleanup Orphaned Preview Containers" workflow
3. Click "Run workflow"
4. The workflow will:
   - Find all preview containers
   - Check if their corresponding PRs are closed
   - Clean up orphaned containers
   - Run the standard cleanup script
   - Show remaining containers

### 2. Cleanup Script (`cleanup-orphaned-containers.sh`)

A script that finds and removes containers that don't have a corresponding preview directory.

**Usage on VPS:**
```bash
# With GitHub API (checks PR status)
bash /opt/maigie/scripts/cleanup-orphaned-containers.sh "GITHUB_TOKEN" "repo-owner" "repo-name"

# Without GitHub API (removes containers without directories)
bash /opt/maigie/scripts/cleanup-orphaned-containers.sh
```

**What it does:**
- Finds all containers with "preview" in the name
- Checks if preview directory exists in `/opt/maigie/previews`
- If directory is missing, checks PR status (if GitHub token provided)
- Removes containers, volumes, Nginx configs, and Docker images
- Prunes unused Docker resources

### 3. Improved Cleanup Workflow

The existing cleanup workflow (`cleanup-preview` job) has been improved to:
- Remove containers even if the directory doesn't exist (handles orphaned containers)
- More robust error handling

### 4. Standard Cleanup Script (`cleanup-previews.sh`)

The existing cleanup script that runs via cron. Make sure it's configured:

**Cron setup:**
```bash
# Edit crontab
crontab -e

# Add this line (runs daily at 2 AM)
0 2 * * * CLOUDFLARE_ACCOUNT_ID="your-account-id" CLOUDFLARE_TUNNEL_ID="your-tunnel-id" CLOUDFLARE_API_TOKEN="your-token" CLOUDFLARE_ZONE_ID="your-zone-id" PREVIEW_DOMAIN="maigie.com" /opt/maigie/scripts/cleanup-previews.sh "GITHUB_TOKEN" "repo-owner" "repo-name" >> /var/log/maigie-cleanup.log 2>&1
```

## Quick Fix: Manual Cleanup on VPS

If you need to clean up containers immediately:

```bash
# SSH into VPS
ssh user@your-vps

# List all preview containers
docker ps -a --filter "name=preview"

# For each orphaned container, remove it:
docker stop maigie-preview-backend-pr-XXX
docker rm maigie-preview-backend-pr-XXX maigie-preview-postgres-pr-XXX maigie-preview-redis-pr-XXX
docker volume rm pr-XXX_postgres_data
docker rmi maigie-backend-preview:pr-XXX

# Or run the cleanup script
bash /opt/maigie/scripts/cleanup-orphaned-containers.sh "GITHUB_TOKEN" "repo-owner" "repo-name"
```

## Prevention

To prevent orphaned containers in the future:

1. **Ensure cleanup workflow runs**: The `cleanup-preview` job should trigger automatically when PRs are closed
2. **Set up cron job**: Configure the cleanup script to run daily
3. **Monitor logs**: Check `/var/log/maigie-cleanup.log` and `/var/log/maigie-cleanup-orphaned.log` regularly
4. **Run manual cleanup periodically**: Use the manual workflow or script monthly to catch any missed containers

## Troubleshooting

### Containers still running after cleanup

1. Check if containers are actually running:
   ```bash
   docker ps --filter "name=preview"
   ```

2. Check cleanup logs:
   ```bash
   tail -100 /var/log/maigie-cleanup.log
   tail -100 /var/log/maigie-cleanup-orphaned.log
   ```

3. Manually remove stubborn containers:
   ```bash
   docker rm -f $(docker ps -a --filter "name=preview" --format "{{.Names}}")
   ```

4. Check if cron job is running:
   ```bash
   crontab -l
   ```

### Cleanup workflow not triggering

- Check workflow permissions in GitHub Actions
- Verify the workflow file is in `.github/workflows/`
- Check if the PR was closed before the workflow was added
- Manually trigger the cleanup workflow
