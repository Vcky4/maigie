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
# Cleanup script for orphaned preview containers
# This script finds and removes all preview containers that are running
# but don't have a corresponding directory in /opt/maigie/previews
# Usage: bash scripts/cleanup-orphaned-containers.sh [GITHUB_TOKEN] [REPO_OWNER] [REPO_NAME]

set -e

PREVIEW_DIR="/opt/maigie/previews"
LOG_FILE="/var/log/maigie-cleanup-orphaned.log"
GITHUB_TOKEN="${1:-}"
REPO_OWNER="${2:-}"
REPO_NAME="${3:-}"

# Create log file if it doesn't exist
touch "$LOG_FILE"

echo "[$(date)] Starting orphaned container cleanup..." >> "$LOG_FILE"

# Function to check if PR is closed/merged
check_pr_status() {
    local pr_number=$1
    if [ -z "$GITHUB_TOKEN" ] || [ -z "$REPO_OWNER" ] || [ -z "$REPO_NAME" ]; then
        echo "unknown"
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

# Function to cleanup orphaned containers
cleanup_orphaned_container() {
    local container_name=$1
    local preview_id=$2
    
    echo "[$(date)] Cleaning up orphaned container: $container_name (Preview ID: $preview_id)" >> "$LOG_FILE"
    
    # Stop and remove container
    docker stop "$container_name" 2>&1 >> "$LOG_FILE" || true
    docker rm -f "$container_name" 2>&1 >> "$LOG_FILE" || true
    
    # Try to remove related containers
    docker rm -f "maigie-preview-backend-${preview_id}" "maigie-preview-postgres-${preview_id}" "maigie-preview-redis-${preview_id}" 2>&1 >> "$LOG_FILE" || true
    
    # Remove volumes
    docker volume rm "${preview_id}_postgres_data" 2>&1 >> "$LOG_FILE" || true
    
    # Remove Nginx config if it exists
    NGINX_CONFIG="/www/server/panel/vhost/nginx/${preview_id}.preview.conf"
    if [ -f "$NGINX_CONFIG" ]; then
        sudo rm -f "$NGINX_CONFIG"
        sudo nginx -t && sudo systemctl reload nginx 2>&1 >> "$LOG_FILE" || echo "[$(date)] Warning: Failed to reload Nginx" >> "$LOG_FILE"
        echo "[$(date)] Removed Nginx config: $NGINX_CONFIG" >> "$LOG_FILE"
    fi
    
    # Remove Docker image if it exists
    docker rmi "maigie-backend-preview:${preview_id}" 2>&1 >> "$LOG_FILE" || true
    
    echo "[$(date)] Cleaned up orphaned container: $container_name" >> "$LOG_FILE"
}

# Find all running preview containers
echo "[$(date)] Searching for preview containers..." >> "$LOG_FILE"

# Get all containers with "preview" in the name
PREVIEW_CONTAINERS=$(docker ps -a --filter "name=preview" --format "{{.Names}}" 2>/dev/null || echo "")

if [ -z "$PREVIEW_CONTAINERS" ]; then
    echo "[$(date)] No preview containers found" >> "$LOG_FILE"
    exit 0
fi

# Process each container
echo "$PREVIEW_CONTAINERS" | while read container_name; do
    if [ -z "$container_name" ]; then
        continue
    fi
    
    # Extract preview ID from container name
    # Format: maigie-preview-backend-pr-123 or maigie-preview-postgres-pr-123
    if [[ "$container_name" =~ maigie-preview-.*-(pr-[0-9]+) ]]; then
        preview_id="${BASH_REMATCH[1]}"
    elif [[ "$container_name" =~ (pr-[0-9]+) ]]; then
        preview_id="${BASH_REMATCH[1]}"
    else
        echo "[$(date)] Warning: Could not extract preview ID from container: $container_name" >> "$LOG_FILE"
        continue
    fi
    
    # Check if preview directory exists
    if [ -d "$PREVIEW_DIR/$preview_id" ]; then
        echo "[$(date)] Keeping container: $container_name (Preview directory exists: $PREVIEW_DIR/$preview_id)" >> "$LOG_FILE"
        continue
    fi
    
    # If GitHub API is available, check PR status
    if [ -n "$GITHUB_TOKEN" ] && [ -n "$REPO_OWNER" ] && [ -n "$REPO_NAME" ]; then
        if [[ "$preview_id" =~ pr-([0-9]+) ]]; then
            pr_number="${BASH_REMATCH[1]}"
            pr_status=$(check_pr_status "$pr_number")
            
            if [ "$pr_status" = "closed" ]; then
                echo "[$(date)] PR #$pr_number is closed, cleaning up container: $container_name" >> "$LOG_FILE"
                cleanup_orphaned_container "$container_name" "$preview_id"
            elif [ "$pr_status" = "open" ]; then
                echo "[$(date)] Warning: Container $container_name exists but preview directory is missing. PR #$pr_number is still open." >> "$LOG_FILE"
                echo "[$(date)] Cleaning up orphaned container anyway (directory missing)." >> "$LOG_FILE"
                cleanup_orphaned_container "$container_name" "$preview_id"
            else
                echo "[$(date)] Could not determine PR status for $preview_id, cleaning up orphaned container: $container_name" >> "$LOG_FILE"
                cleanup_orphaned_container "$container_name" "$preview_id"
            fi
        else
            echo "[$(date)] Could not extract PR number from $preview_id, cleaning up orphaned container: $container_name" >> "$LOG_FILE"
            cleanup_orphaned_container "$container_name" "$preview_id"
        fi
    else
        # Without GitHub API, clean up if directory doesn't exist
        echo "[$(date)] Preview directory missing for $preview_id, cleaning up orphaned container: $container_name" >> "$LOG_FILE"
        cleanup_orphaned_container "$container_name" "$preview_id"
    fi
done

# Also check for containers that might be stopped but not removed
STOPPED_CONTAINERS=$(docker ps -a --filter "name=preview" --filter "status=exited" --format "{{.Names}}" 2>/dev/null || echo "")

if [ -n "$STOPPED_CONTAINERS" ]; then
    echo "$STOPPED_CONTAINERS" | while read container_name; do
        if [ -z "$container_name" ]; then
            continue
        fi
        
        # Extract preview ID
        if [[ "$container_name" =~ maigie-preview-.*-(pr-[0-9]+) ]]; then
            preview_id="${BASH_REMATCH[1]}"
        elif [[ "$container_name" =~ (pr-[0-9]+) ]]; then
            preview_id="${BASH_REMATCH[1]}"
        else
            continue
        fi
        
        # Remove if directory doesn't exist
        if [ ! -d "$PREVIEW_DIR/$preview_id" ]; then
            echo "[$(date)] Removing stopped container: $container_name" >> "$LOG_FILE"
            docker rm -f "$container_name" 2>&1 >> "$LOG_FILE" || true
        fi
    done
fi

# Prune unused Docker resources
echo "[$(date)] Pruning unused Docker resources..." >> "$LOG_FILE"
docker system prune -af --volumes --filter "until=168h" >> "$LOG_FILE" 2>&1 || true

echo "[$(date)] Orphaned container cleanup complete!" >> "$LOG_FILE"
echo "[$(date)] Check $LOG_FILE for details" >> "$LOG_FILE"
