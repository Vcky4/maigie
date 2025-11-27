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
# Cloudflare Tunnel Route Management Script
# Manages dynamic routes for preview environments via Cloudflare API
# Usage:
#   Add route:   bash scripts/cloudflare-tunnel-routes.sh add <hostname> <service>
#   Remove route: bash scripts/cloudflare-tunnel-routes.sh remove <hostname>
#   List routes: bash scripts/cloudflare-tunnel-routes.sh list

set -e

ACTION="${1:-}"
HOSTNAME="${2:-}"
SERVICE="${3:-http://localhost:80}"

# Required environment variables
CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"
CLOUDFLARE_TUNNEL_ID="${CLOUDFLARE_TUNNEL_ID:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

if [ -z "$CLOUDFLARE_ACCOUNT_ID" ] || [ -z "$CLOUDFLARE_TUNNEL_ID" ] || [ -z "$CLOUDFLARE_API_TOKEN" ]; then
    echo "Error: Missing required environment variables"
    echo "Required: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_TUNNEL_ID, CLOUDFLARE_API_TOKEN"
    exit 1
fi

API_BASE="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${CLOUDFLARE_TUNNEL_ID}"

case "$ACTION" in
    add)
        if [ -z "$HOSTNAME" ]; then
            echo "Error: Hostname required for add action"
            echo "Usage: $0 add <hostname> [service]"
            exit 1
        fi
        
        echo "Adding route: $HOSTNAME -> $SERVICE"
        
        # Get current config
        CURRENT_CONFIG=$(curl -s -X GET \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            "${API_BASE}/configurations")
        
        # Extract current ingress rules
        INGRESS_RULES=$(echo "$CURRENT_CONFIG" | jq -r '.result.config.ingress // []')
        
        # Check if hostname already exists
        if echo "$INGRESS_RULES" | jq -e ".[] | select(.hostname == \"$HOSTNAME\")" > /dev/null 2>&1; then
            echo "Route $HOSTNAME already exists, updating..."
            # Remove existing route
            INGRESS_RULES=$(echo "$INGRESS_RULES" | jq "map(select(.hostname != \"$HOSTNAME\"))")
        fi
        
        # Separate specific rules from catch-all rules
        SPECIFIC_RULES=$(echo "$INGRESS_RULES" | jq "[.[] | select(.hostname != null and .hostname != \"\")]")
        CATCH_ALL_RULES=$(echo "$INGRESS_RULES" | jq "[.[] | select(.hostname == null or .hostname == \"\")]")
        
        # Add new route to specific rules, then append catch-all rules at the end
        NEW_RULE="{\"hostname\":\"$HOSTNAME\",\"service\":\"$SERVICE\"}"
        INGRESS_RULES=$(echo "$SPECIFIC_RULES" | jq ". + [$NEW_RULE] | . + $CATCH_ALL_RULES")
        
        # Update config
        UPDATE_RESPONSE=$(curl -s -X PUT \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"config\":{\"ingress\":$INGRESS_RULES}}" \
            "${API_BASE}/configurations")
        
        if echo "$UPDATE_RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
            echo "✓ Successfully added route: $HOSTNAME -> $SERVICE"
        else
            echo "✗ Failed to add route"
            echo "$UPDATE_RESPONSE" | jq '.'
            exit 1
        fi
        ;;
        
    remove)
        if [ -z "$HOSTNAME" ]; then
            echo "Error: Hostname required for remove action"
            echo "Usage: $0 remove <hostname>"
            exit 1
        fi
        
        echo "Removing route: $HOSTNAME"
        
        # Get current config
        CURRENT_CONFIG=$(curl -s -X GET \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            "${API_BASE}/configurations")
        
        # Extract current ingress rules
        INGRESS_RULES=$(echo "$CURRENT_CONFIG" | jq -r '.result.config.ingress // []')
        
        # Check if hostname exists
        if ! echo "$INGRESS_RULES" | jq -e ".[] | select(.hostname == \"$HOSTNAME\")" > /dev/null 2>&1; then
            echo "Route $HOSTNAME does not exist, skipping..."
            exit 0
        fi
        
        # Remove route
        INGRESS_RULES=$(echo "$INGRESS_RULES" | jq "map(select(.hostname != \"$HOSTNAME\"))")
        
        # Update config
        UPDATE_RESPONSE=$(curl -s -X PUT \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"config\":{\"ingress\":$INGRESS_RULES}}" \
            "${API_BASE}/configurations")
        
        if echo "$UPDATE_RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
            echo "✓ Successfully removed route: $HOSTNAME"
        else
            echo "✗ Failed to remove route"
            echo "$UPDATE_RESPONSE" | jq '.'
            exit 1
        fi
        ;;
        
    list)
        echo "Current tunnel routes:"
        
        CURRENT_CONFIG=$(curl -s -X GET \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            "${API_BASE}/configurations")
        
        echo "$CURRENT_CONFIG" | jq -r '.result.config.ingress[]? | "\(.hostname // "catch-all") -> \(.service)"'
        ;;
        
    *)
        echo "Usage: $0 {add|remove|list} [hostname] [service]"
        echo ""
        echo "Examples:"
        echo "  $0 add pr-44.preview.maigie.com http://localhost:80"
        echo "  $0 remove pr-44.preview.maigie.com"
        echo "  $0 list"
        exit 1
        ;;
esac

