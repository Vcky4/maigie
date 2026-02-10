"""
Usage tracking service for managing Free tier feature limits.

This module handles:
- File upload limit tracking (5/month for FREE tier)
- AI summary generation limit tracking
- Monthly period reset for usage counters

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from prisma import Prisma
from prisma.models import User

from ..core.database import db
from ..utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

# Feature limits per tier
FEATURE_LIMITS = {
    "FREE": {
        "file_uploads": 5,  # 5 files per month
        "summary_generations": 10,  # 10 summaries per month
    },
    "PREMIUM_MONTHLY": {
        "file_uploads": None,  # Unlimited
        "summary_generations": None,  # Unlimited
    },
    "PREMIUM_YEARLY": {
        "file_uploads": None,  # Unlimited
        "summary_generations": None,  # Unlimited
    },
}


async def get_feature_limit(tier: str, feature: str) -> Optional[int]:
    """
    Get the limit for a specific feature and tier.

    Args:
        tier: User tier (FREE, PREMIUM_MONTHLY, PREMIUM_YEARLY)
        feature: Feature name (file_uploads, summary_generations)

    Returns:
        Limit value or None if unlimited
    """
    limits = FEATURE_LIMITS.get(tier, FEATURE_LIMITS["FREE"])
    return limits.get(feature)


async def ensure_usage_period(user: User, feature: str, db_client: Prisma | None = None) -> User:
    """
    Ensure user has an active usage period for a feature. Reset if period expired.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object with valid usage period
    """
    if db_client is None:
        db_client = db

    now = datetime.utcnow()
    tier_str = str(user.tier) if user.tier else "FREE"

    # Premium users don't need tracking
    if tier_str != "FREE":
        return user

    # Determine which period field to check based on feature
    if feature == "file_uploads":
        period_start = user.fileUploadsPeriodStart
        count_field = "fileUploadsCount"
        period_start_field = "fileUploadsPeriodStart"
    elif feature == "summary_generations":
        period_start = user.summaryGenerationsPeriodStart
        count_field = "summaryGenerationsCount"
        period_start_field = "summaryGenerationsPeriodStart"
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Check if period needs to be initialized or reset
    needs_reset = False

    if period_start is None:
        needs_reset = True
    else:
        # Reset if period has expired (30 days)
        period_end = period_start + timedelta(days=30)
        if now >= period_end:
            needs_reset = True

    if needs_reset:
        update_data = {
            count_field: 0,
            period_start_field: now,
        }
        user = await db_client.user.update(where={"id": user.id}, data=update_data)
        logger.info(f"Reset {feature} usage period for user {user.id}")

    return user


async def check_feature_limit(
    user: User, feature: str, db_client: Prisma | None = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if user can use a feature (hasn't reached limit).

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Tuple of (can_use, error_message)
        - can_use: True if feature can be used, False if limit reached
        - error_message: Optional error message if limit reached
    """
    if db_client is None:
        db_client = db

    tier_str = str(user.tier) if user.tier else "FREE"
    limit = await get_feature_limit(tier_str, feature)

    # Premium users have unlimited access
    if limit is None:
        return True, None

    # Ensure usage period is active
    user = await ensure_usage_period(user, feature, db_client)

    # Refresh user from database to get latest counts
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    # Get current count based on feature
    if feature == "file_uploads":
        current_count = user.fileUploadsCount or 0
        feature_name = "file uploads"
    elif feature == "summary_generations":
        current_count = user.summaryGenerationsCount or 0
        feature_name = "AI summaries"
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Check if limit reached
    if current_count >= limit:
        period_start = (
            user.fileUploadsPeriodStart
            if feature == "file_uploads"
            else user.summaryGenerationsPeriodStart
        )
        if period_start:
            period_end = period_start + timedelta(days=30)
            reset_date = period_end.strftime("%B %d, %Y")
        else:
            reset_date = "next month"

        error_message = (
            f"You've reached your monthly limit of {limit} {feature_name}. "
            f"Your limit will reset on {reset_date}. "
            f"Upgrade to Premium for unlimited {feature_name}."
        )
        return False, error_message

    return True, None


async def increment_feature_usage(
    user: User, feature: str, db_client: Prisma | None = None
) -> User:
    """
    Increment usage counter for a feature.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object
    """
    if db_client is None:
        db_client = db

    # Check limit before incrementing
    can_use, error_message = await check_feature_limit(user, feature, db_client)
    if not can_use:
        raise SubscriptionLimitError(
            message=error_message or f"Limit reached for {feature}",
            detail=f"Upgrade to Premium for unlimited {feature}.",
        )

    # Determine which field to increment
    if feature == "file_uploads":
        update_data = {"fileUploadsCount": {"increment": 1}}
    elif feature == "summary_generations":
        update_data = {"summaryGenerationsCount": {"increment": 1}}
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Increment counter
    user = await db_client.user.update(where={"id": user.id}, data=update_data)
    logger.info(f"Incremented {feature} usage for user {user.id}")

    return user


async def get_feature_usage(user: User, feature: str, db_client: Prisma | None = None) -> dict:
    """
    Get current usage information for a feature.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Dictionary with usage information
    """
    if db_client is None:
        db_client = db

    tier_str = str(user.tier) if user.tier else "FREE"
    limit = await get_feature_limit(tier_str, feature)

    # Ensure period is active
    user = await ensure_usage_period(user, feature, db_client)

    # Refresh user from database
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    # Get current count
    if feature == "file_uploads":
        current_count = user.fileUploadsCount or 0
        period_start = user.fileUploadsPeriodStart
    elif feature == "summary_generations":
        current_count = user.summaryGenerationsCount or 0
        period_start = user.summaryGenerationsPeriodStart
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Calculate period end
    if period_start:
        period_end = period_start + timedelta(days=30)
        period_end_str = period_end.isoformat()
    else:
        period_end_str = None

    return {
        "used": current_count,
        "limit": limit,
        "remaining": (limit - current_count) if limit else None,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end_str,
        "is_unlimited": limit is None,
    }
