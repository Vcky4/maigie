"""
Audit service for logging admin actions.

This module handles:
- Logging admin actions
- Tracking user activities
- Audit trail for compliance

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Optional

from prisma import Prisma

from ..core.database import db

logger = logging.getLogger(__name__)


async def log_admin_action(
    admin_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    db_client: Prisma | None = None,
) -> None:
    """
    Log an admin action for audit purposes.

    Args:
        admin_user_id: ID of the admin user performing the action
        action: Action performed (e.g., "create_user", "delete_course")
        resource_type: Type of resource affected (e.g., "user", "course")
        resource_id: ID of the resource affected (optional)
        details: Additional details about the action (optional)
        db_client: Optional Prisma client (defaults to global db)
    """
    if db_client is None:
        db_client = db

    try:
        # Store in database for production audit trail
        timestamp = datetime.now(UTC)
        await db_client.auditlog.create(
            data={
                "adminUserId": admin_user_id,
                "actionType": action,
                "resourceType": resource_type,
                "resourceId": resource_id,
                "details": details,
                "timestamp": timestamp,
            }
        )

        # Also log to application logs for immediate visibility
        log_message = (
            f"ADMIN_ACTION: user={admin_user_id}, action={action}, "
            f"resource_type={resource_type}, resource_id={resource_id}"
        )
        if details:
            log_message += f", details={details}"

        logger.info(
            log_message,
            extra={
                "audit": True,
                "admin_user_id": admin_user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": details,
                "timestamp": timestamp.isoformat(),
            },
        )
    except Exception as e:
        # Log error but don't fail the operation
        logger.error(f"Failed to log admin action to database: {e}", exc_info=True)


async def log_user_activity(
    user_id: str,
    activity_type: str,
    details: dict | None = None,
    db_client: Prisma | None = None,
) -> None:
    """
    Log user activity for monitoring purposes.

    Args:
        user_id: ID of the user
        activity_type: Type of activity (e.g., "login", "logout", "api_call")
        details: Additional details about the activity (optional)
        db_client: Optional Prisma client (defaults to global db)
    """
    if db_client is None:
        db_client = db

    log_message = f"USER_ACTIVITY: user={user_id}, activity_type={activity_type}"
    if details:
        log_message += f", details={details}"

    timestamp = datetime.now(UTC)
    logger.info(
        log_message,
        extra={
            "audit": True,
            "user_id": user_id,
            "activity_type": activity_type,
            "details": details,
            "timestamp": timestamp.isoformat(),
        },
    )
