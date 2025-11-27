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
# Cleanup script for old preview environments
# Run this periodically (via cron) to clean up preview environments:
# - Previews older than 3 days (if PR is still open)
# - All previews for closed/merged PRs
# Usage: bash scripts/cleanup-previews.sh [GITHUB_TOKEN] [REPO_OWNER] [REPO_NAME]

set -e

PREVIEW_DIR="/opt/maigie/previews"
MAX_AGE_DAYS=3
LOG_FILE="/var/log/maigie-cleanup.log"
GITHUB_TOKEN="${1:-}"
REPO_OWNER="${2:-}"
REPO_NAME="${3:-}"

# Cloudflare API credentials (optional, for tunnel route and DNS cleanup)
CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"
CLOUDFLARE_TUNNEL_ID="${CLOUDFLARE_TUNNEL_ID:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CLOUDFLARE_ZONE_ID="${CLOUDFLARE_ZONE_ID:-}"
PREVIEW_DOMAIN="${PREVIEW_DOMAIN:-maigie.com}"

# Create log file if it doesn't exist
touch "$LOG_FILE"

echo "[$(date)] Starting preview cleanup..." >> "$LOG_FILE"
echo "[$(date)] Max age for open PRs: $MAX_AGE_DAYS days" >> "$LOG_FILE"

if [ ! -d "$PREVIEW_DIR" ]; then
    echo "[$(date)] Preview directory does not exist: $PREVIEW_DIR" >> "$LOG_FILE"
    exit 0
fi

# Function to check if PR is closed/merged
check_pr_status() {
    local pr_number=$1
    if [ -z "$GITHUB_TOKEN" ] || [ -z "$REPO_OWNER" ] || [ -z "$REPO_NAME" ]; then
        # If GitHub API not available, assume PR is open
        echo "open"
        return
    fi
    
    local response=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
        "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/pulls/$pr_number" 2>/dev/null || echo "")
    
    if [ -z "$response" ]; then
        echo "unknown"
        return
    fi
    
    local state=$(echo "$response" | grep -o '"state":"[^"]*"' | cut -d'"' -f4)
    local merged=$(echo "$response" | grep -o '"merged":[^,]*' | cut -d':' -f2)
    
    if [ "$state" = "closed" ] || [ "$merged" = "true" ]; then
        echo "closed"
    else
        echo "open"
    fi
}

# Function to cleanup preview
cleanup_preview() {
    local preview_id=$1
    local reason=$2
    
    echo "[$(date)] Cleaning up preview: $preview_id (Reason: $reason)" >> "$LOG_FILE"
    
    cd "$PREVIEW_DIR/$preview_id" 2>/dev/null || return
    
    # Stop and remove containers
    if [ -f "docker-compose.yml" ]; then
        docker-compose down -v 2>&1 >> "$LOG_FILE" || echo "[$(date)] Warning: Failed to stop containers for $preview_id" >> "$LOG_FILE"
    fi
    
    # Remove any remaining containers
    docker rm -f "maigie-preview-backend-${preview_id}" "maigie-preview-postgres-${preview_id}" "maigie-preview-redis-${preview_id}" 2>&1 >> "$LOG_FILE" || true
    
    # Remove volumes with preview-specific naming
    docker volume rm "${preview_id}_postgres_data" 2>&1 >> "$LOG_FILE" || true
    
    # Remove Nginx config if it exists
    NGINX_CONFIG="/www/server/panel/vhost/nginx/${preview_id}.preview.conf"
    if [ -f "$NGINX_CONFIG" ]; then
        sudo rm -f "$NGINX_CONFIG"
        sudo nginx -t && sudo systemctl reload nginx 2>&1 >> "$LOG_FILE" || echo "[$(date)] Warning: Failed to reload Nginx after removing config" >> "$LOG_FILE"
        echo "[$(date)] Removed Nginx config: $NGINX_CONFIG" >> "$LOG_FILE"
    fi
    
    # Remove Cloudflare Tunnel route and DNS record if API credentials are available
    if [ -n "$CLOUDFLARE_ACCOUNT_ID" ] && [ -n "$CLOUDFLARE_TUNNEL_ID" ] && [ -n "$CLOUDFLARE_API_TOKEN" ] && [ -n "$CLOUDFLARE_ZONE_ID" ]; then
        PREVIEW_DOMAIN_VALUE="${PREVIEW_DOMAIN:-maigie.com}"
        PREVIEW_DOMAIN="${preview_id}-api-preview.${PREVIEW_DOMAIN_VALUE}"
        
        echo "[$(date)] Removing Cloudflare Tunnel route and DNS record: $PREVIEW_DOMAIN" >> "$LOG_FILE"
        
        # Remove DNS record
        DNS_NAME=$(echo "${PREVIEW_DOMAIN}" | cut -d. -f1)
        EXISTING_RECORD=$(curl -s -X GET \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?name=${PREVIEW_DOMAIN}&type=CNAME" 2>&1 | \
            jq -r '.result[0].id // empty' 2>/dev/null)
        
        if [ -n "$EXISTING_RECORD" ] && [ "$EXISTING_RECORD" != "null" ]; then
            DNS_DELETE_RESPONSE=$(curl -s -X DELETE \
                -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
                -H "Content-Type: application/json" \
                "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${EXISTING_RECORD}" 2>&1)
            
            if echo "$DNS_DELETE_RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
                echo "[$(date)] Successfully removed DNS record: $PREVIEW_DOMAIN" >> "$LOG_FILE"
            else
                echo "[$(date)] Warning: Failed to remove DNS record: $PREVIEW_DOMAIN" >> "$LOG_FILE"
            fi
        fi
        
        # Remove tunnel route
        CURRENT_CONFIG=$(curl -s -X GET \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${CLOUDFLARE_TUNNEL_ID}/configurations" 2>&1)
        
        # Extract current ingress rules
        INGRESS_RULES=$(echo "$CURRENT_CONFIG" | jq -r '.result.config.ingress // []' 2>/dev/null)
        
        if [ -n "$INGRESS_RULES" ] && [ "$INGRESS_RULES" != "null" ]; then
            # Check if hostname exists
            if echo "$INGRESS_RULES" | jq -e ".[] | select(.hostname == \"${PREVIEW_DOMAIN}\")" > /dev/null 2>&1; then
                # Remove route
                INGRESS_RULES=$(echo "$INGRESS_RULES" | jq "map(select(.hostname != \"${PREVIEW_DOMAIN}\"))")
                
                # Update config
                UPDATE_RESPONSE=$(curl -s -X PUT \
                    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
                    -H "Content-Type: application/json" \
                    -d "{\"config\":{\"ingress\":$INGRESS_RULES}}" \
                    "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${CLOUDFLARE_TUNNEL_ID}/configurations" 2>&1)
                
                if echo "$UPDATE_RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
                    echo "[$(date)] Successfully removed Cloudflare Tunnel route: $PREVIEW_DOMAIN" >> "$LOG_FILE"
                else
                    echo "[$(date)] Warning: Failed to remove Cloudflare Tunnel route: $PREVIEW_DOMAIN" >> "$LOG_FILE"
                fi
            fi
        fi
    fi
    
    # Remove directory
    cd "$PREVIEW_DIR"
    rm -rf "$preview_id"
    echo "[$(date)] Removed preview directory: $preview_id" >> "$LOG_FILE"
}

# Process each preview directory
find "$PREVIEW_DIR" -mindepth 1 -maxdepth 1 -type d | while read dir; do
    PREVIEW_ID=$(basename "$dir")
    
    # Extract PR number from preview ID (format: pr-123)
    if [[ "$PREVIEW_ID" =~ ^pr-([0-9]+)$ ]]; then
        PR_NUMBER="${BASH_REMATCH[1]}"
        
        # Check PR status
        PR_STATUS=$(check_pr_status "$PR_NUMBER")
        
        # Check directory age
        DIR_AGE_DAYS=$(( ($(date +%s) - $(stat -c %Y "$dir")) / 86400 ))
        
        # Cleanup if:
        # 1. PR is closed/merged (immediate cleanup)
        # 2. PR is open but preview is older than MAX_AGE_DAYS
        if [ "$PR_STATUS" = "closed" ]; then
            cleanup_preview "$PREVIEW_ID" "PR #$PR_NUMBER is closed/merged"
        elif [ "$DIR_AGE_DAYS" -ge "$MAX_AGE_DAYS" ]; then
            cleanup_preview "$PREVIEW_ID" "Preview is $DIR_AGE_DAYS days old (max: $MAX_AGE_DAYS days)"
        else
            echo "[$(date)] Keeping preview: $PREVIEW_ID (PR #$PR_NUMBER is $PR_STATUS, age: $DIR_AGE_DAYS days)" >> "$LOG_FILE"
        fi
    else
        # If preview ID doesn't match expected format, check age only
        DIR_AGE_DAYS=$(( ($(date +%s) - $(stat -c %Y "$dir")) / 86400 ))
        if [ "$DIR_AGE_DAYS" -ge "$MAX_AGE_DAYS" ]; then
            cleanup_preview "$PREVIEW_ID" "Preview is $DIR_AGE_DAYS days old (unknown PR)"
        fi
    fi
done

# Prune unused Docker resources (images, containers, volumes, networks older than 7 days)
echo "[$(date)] Pruning unused Docker resources..." >> "$LOG_FILE"
docker system prune -af --volumes --filter "until=168h" >> "$LOG_FILE" 2>&1 || true

echo "[$(date)] Cleanup complete!" >> "$LOG_FILE"

