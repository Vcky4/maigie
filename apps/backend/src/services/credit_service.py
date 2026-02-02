"""
Credit service for managing subscription credit limits.

This module handles:
- Credit consumption tracking
- Hard/soft cap enforcement
- Credit period management
- Fair usage prevention

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from prisma import Prisma
from prisma.models import User

from ..config import Settings, get_settings
from ..core.database import db
from ..services.referral_service import get_daily_limit_increase
from ..utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

settings = get_settings()


# Credit limits per tier (in tokens/credits)
# These represent monthly limits for monthly subscriptions
# For yearly subscriptions, multiply by 12
CREDIT_LIMITS = {
    "FREE": {
        "hard_cap": 50000,  # 50k tokens/month (increased from 10k)
        "soft_cap": 40000,  # 80% warning threshold
        "daily_limit": 15000,  # 15k tokens/day (increased from 5k)
    },
    "PREMIUM_MONTHLY": {
        "hard_cap": 200000,  # 200k tokens/month (increased from 100k)
        "soft_cap": 160000,  # 80% warning threshold
    },
    "PREMIUM_YEARLY": {
        "hard_cap": 2400000,  # 2.4M tokens/year (200k/month * 12)
        "soft_cap": 1920000,  # 80% warning threshold
    },
}

# Token multiplier - we charge users less than actual tokens consumed
# This makes the service more affordable while still tracking usage
# 0.2 = charge 20% of actual tokens (users get 5x the conversations)
TOKEN_MULTIPLIER = 0.2

# Credit costs for different operations (in tokens) - Reduced for cost optimization
CREDIT_COSTS = {
    "ai_course_generation": 250,  # 250 tokens per AI course generation (reduced from 500)
    "chat_message": 0,  # Tracked separately via tokenCount in ChatMessage
    "ai_action": 100,  # 100 tokens per AI action (reduced from 250)
}


async def get_credit_limits(tier: str) -> dict[str, int]:
    """
    Get credit limits for a given tier.

    Args:
        tier: User tier (FREE, PREMIUM_MONTHLY, PREMIUM_YEARLY)

    Returns:
        Dictionary with 'hard_cap' and 'soft_cap' values
    """
    tier_str = str(tier) if tier else "FREE"
    return CREDIT_LIMITS.get(tier_str, CREDIT_LIMITS["FREE"])


async def initialize_user_credits(
    user: User, period_start: datetime | None = None, period_end: datetime | None = None
) -> User:
    """
    Initialize or reset user credits for a new billing period.

    Args:
        user: User model instance
        period_start: Start of credit period (defaults to now)
        period_end: End of credit period (defaults to now + 1 month/year based on tier)

    Returns:
        Updated User object
    """
    tier_str = str(user.tier) if user.tier else "FREE"
    limits = await get_credit_limits(tier_str)

    # Determine period duration based on tier
    if tier_str == "PREMIUM_YEARLY":
        period_duration = timedelta(days=365)
    else:
        period_duration = timedelta(days=30)  # Monthly for FREE and PREMIUM_MONTHLY

    # Set period start/end
    if period_start is None:
        period_start = datetime.utcnow()
    if period_end is None:
        period_end = period_start + period_duration

    # Prepare update data
    update_data = {
        "creditsUsed": 0,
        "creditsPeriodStart": period_start,
        "creditsPeriodEnd": period_end,
        "creditsSoftCap": limits["soft_cap"],
        "creditsHardCap": limits["hard_cap"],
    }

    # For FREE tier, also initialize daily limits
    if tier_str == "FREE" and "daily_limit" in limits:
        update_data["creditsDailyLimit"] = limits["daily_limit"]
        update_data["creditsUsedToday"] = 0
        update_data["lastDailyReset"] = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )  # Set to midnight UTC

    # Update user with credit limits and reset usage
    updated_user = await db.user.update(where={"id": user.id}, data=update_data)

    logger.info(
        f"Initialized credits for user {user.id} (tier: {tier_str}): "
        f"hard_cap={limits['hard_cap']}, soft_cap={limits['soft_cap']}"
        + (f", daily_limit={limits.get('daily_limit', 'N/A')}" if tier_str == "FREE" else "")
    )

    return updated_user


async def reset_daily_credits_if_needed(user: User, db_client: Prisma | None = None) -> User:
    """
    Reset daily credits if a new day has started (for FREE tier users).

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object
    """
    if db_client is None:
        db_client = db

    tier_str = str(user.tier) if user.tier else "FREE"
    if tier_str != "FREE":
        # Daily limits only apply to FREE tier
        return user

    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Check if daily reset is needed
    needs_daily_reset = False

    last_reset = user.lastDailyReset
    if last_reset and last_reset.tzinfo:
        last_reset = last_reset.replace(tzinfo=None)

    if last_reset is None:
        needs_daily_reset = True
    elif last_reset < today_midnight:
        # A new day has started since last reset
        needs_daily_reset = True
        logger.info(
            f"Daily credit reset needed for user {user.id}. "
            f"Last reset: {user.lastDailyReset}, Today: {today_midnight}"
        )

    if needs_daily_reset:
        updated_user = await db_client.user.update(
            where={"id": user.id},
            data={
                "creditsUsedToday": 0,
                "lastDailyReset": today_midnight,
            },
        )
        logger.info(f"Reset daily credits for user {user.id}")
        return updated_user

    return user


async def ensure_credit_period(user: User, db_client: Prisma | None = None) -> User:
    """
    Ensure user has an active credit period. If period has expired or doesn't exist,
    initialize a new one.

    FREE tier: Usage resets monthly when creditsPeriodEnd is reached (every 30 days).
    Limits are synced to current CREDIT_LIMITS when stored limits are outdated
    (e.g. old 10k cap upgraded to 50k) so existing users get new limits without waiting.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object with valid credit period
    """
    if db_client is None:
        db_client = db

    now = datetime.utcnow()
    tier_str = str(user.tier) if user.tier else "FREE"

    # Sync FREE users with outdated stored limits to current tier limits (e.g. 10k -> 50k)
    if tier_str == "FREE":
        current_limits = await get_credit_limits(tier_str)
        stored_hard = user.creditsHardCap or 0
        if stored_hard < current_limits["hard_cap"]:
            update_data = {
                "creditsHardCap": current_limits["hard_cap"],
                "creditsSoftCap": current_limits["soft_cap"],
            }
            if "daily_limit" in current_limits:
                update_data["creditsDailyLimit"] = current_limits["daily_limit"]
            user = await db_client.user.update(where={"id": user.id}, data=update_data)
            logger.info(
                f"Synced FREE user {user.id} limits to current: hard_cap={current_limits['hard_cap']}, "
                f"daily_limit={current_limits.get('daily_limit', 'N/A')}"
            )

    # Check if period needs to be initialized or reset
    needs_reset = False

    current_period_end = user.creditsPeriodEnd
    if current_period_end and current_period_end.tzinfo:
        current_period_end = current_period_end.replace(tzinfo=None)

    if current_period_end is None or user.creditsPeriodStart is None:
        needs_reset = True
    elif current_period_end <= now:
        # Period has expired, reset credits
        needs_reset = True
        logger.info(
            f"Credit period expired for user {user.id}. "
            f"Period end: {user.creditsPeriodEnd}, Now: {now}"
        )

    if needs_reset:
        # Determine new period start/end based on subscription period if available
        period_start = user.subscriptionCurrentPeriodStart
        period_end = user.subscriptionCurrentPeriodEnd

        # Handle timezone awareness for period_start
        if period_start and period_start.tzinfo:
            period_start = period_start.replace(tzinfo=None)

        # Handle timezone awareness for period_end
        if period_end and period_end.tzinfo:
            period_end = period_end.replace(tzinfo=None)

        if period_start is None or period_end is None or period_end <= now:
            period_start = now
            # Set period end based on tier
            tier_str = str(user.tier) if user.tier else "FREE"
            if tier_str == "PREMIUM_YEARLY":
                period_end = now + timedelta(days=365)
            else:
                period_end = now + timedelta(days=30)

        user = await initialize_user_credits(user, period_start, period_end)

    return user


async def check_credit_availability(
    user: User, credits_needed: int, db_client: Prisma | None = None
) -> tuple[bool, str | None]:
    """
    Check if user has enough credits available.

    Args:
        user: User model instance
        credits_needed: Number of credits required (raw tokens - multiplier will be applied)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Tuple of (is_available, warning_message)
        - is_available: True if credits can be consumed, False if hard cap reached
        - warning_message: Optional warning if soft cap is reached
    """
    if db_client is None:
        db_client = db

    # Apply token multiplier to get actual credits that will be consumed
    credits_needed = apply_token_multiplier(credits_needed)

    # Ensure credit period is active
    user = await ensure_credit_period(user, db_client)

    # Reset daily credits if needed (for FREE tier)
    user = await reset_daily_credits_if_needed(user, db_client)

    # Refresh user from database to get latest creditsUsed
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    tier_str = str(user.tier) if user.tier else "FREE"
    hard_cap = user.creditsHardCap or 0
    soft_cap = user.creditsSoftCap or 0
    credits_used = user.creditsUsed or 0

    # For FREE tier, check daily limit first
    if tier_str == "FREE":
        daily_limit = user.creditsDailyLimit or 0
        credits_used_today = user.creditsUsedToday or 0

        # Get daily limit increase from claimed referral rewards
        referral_increase = await get_daily_limit_increase(user, db_client)
        effective_daily_limit = daily_limit + referral_increase

        # Check daily limit first
        if (
            effective_daily_limit > 0
            and credits_used_today + credits_needed > effective_daily_limit
        ):
            return False, None

    # Check monthly hard cap
    if hard_cap > 0 and credits_used + credits_needed > hard_cap:
        return False, None

    # Check soft cap (warning only, doesn't block)
    warning_message = None
    if soft_cap > 0 and credits_used >= soft_cap:
        remaining = hard_cap - credits_used
        warning_message = (
            f"You've reached {soft_cap:,} credits (soft cap). "
            f"You have {remaining:,} credits remaining before hitting the hard cap."
        )

    return True, warning_message


def apply_token_multiplier(tokens: int) -> int:
    """
    Apply token multiplier to reduce the credits charged to users.
    This makes the service more affordable.

    Args:
        tokens: Raw token count

    Returns:
        Adjusted token count after applying multiplier
    """
    adjusted = int(tokens * TOKEN_MULTIPLIER)
    # Minimum of 1 credit if there were any tokens
    return max(1, adjusted) if tokens > 0 else 0


async def consume_credits(
    user: User, credits: int, operation: str = "unknown", db_client: Prisma | None = None
) -> User:
    """
    Consume credits for a user operation.

    Args:
        user: User model instance
        credits: Number of credits to consume (raw tokens - multiplier will be applied)
        operation: Description of the operation (for logging)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object

    Raises:
        SubscriptionLimitError: If hard cap would be exceeded
    """
    if db_client is None:
        db_client = db

    # Apply token multiplier to reduce credits charged
    credits = apply_token_multiplier(credits)

    # Ensure credit period is active
    user = await ensure_credit_period(user, db_client)

    # Reset daily credits if needed (for FREE tier)
    user = await reset_daily_credits_if_needed(user, db_client)

    # Check availability before consuming
    is_available, warning_message = await check_credit_availability(user, credits, db_client)
    if not is_available:
        tier_str = str(user.tier) if user.tier else "FREE"
        hard_cap = user.creditsHardCap or 0
        credits_used = user.creditsUsed or 0

        # For FREE tier, check if it's daily or monthly limit
        if tier_str == "FREE":
            daily_limit = user.creditsDailyLimit or 0
            credits_used_today = user.creditsUsedToday or 0

            # Get daily limit increase from claimed referral rewards
            referral_increase = await get_daily_limit_increase(user, db_client)
            effective_daily_limit = daily_limit + referral_increase

            # Check if daily limit is exceeded
            if effective_daily_limit > 0 and credits_used_today + credits > effective_daily_limit:
                raise SubscriptionLimitError(
                    message=f"Daily credit limit exceeded. You've used {credits_used_today:,} of {effective_daily_limit:,} credits today.",
                    detail=f"This operation requires {credits} credits. Your daily limit resets at midnight UTC. Upgrade to Premium for higher limits.",
                )

        # Monthly limit exceeded
        if tier_str == "FREE":
            raise SubscriptionLimitError(
                message=f"Monthly credit limit exceeded. You've used {credits_used:,} of {hard_cap:,} credits this month.",
                detail=f"This operation requires {credits} credits. Please wait until next month for your credits to reset, or upgrade to Premium for higher limits.",
            )
        else:
            raise SubscriptionLimitError(
                message=f"Monthly credit limit exceeded. You've used {credits_used:,} of {hard_cap:,} credits this month.",
                detail=f"This operation requires {credits} credits. Please wait until your credit period resets or upgrade your plan.",
            )

    # Prepare update data
    update_data = {"creditsUsed": {"increment": credits}}

    # For FREE tier, also increment daily usage
    tier_str = str(user.tier) if user.tier else "FREE"
    if tier_str == "FREE":
        update_data["creditsUsedToday"] = {"increment": credits}

    # Consume credits
    updated_user = await db_client.user.update(where={"id": user.id}, data=update_data)

    logger.info(
        f"Consumed {credits} credits for user {user.id} (operation: {operation}). "
        f"Total used: {updated_user.creditsUsed}/{updated_user.creditsHardCap}"
        + (
            f", Daily used: {updated_user.creditsUsedToday}/{updated_user.creditsDailyLimit}"
            if tier_str == "FREE"
            else ""
        )
    )

    # Log warning if soft cap reached
    if warning_message:
        logger.warning(f"Soft cap warning for user {user.id}: {warning_message}")

    return updated_user


async def get_credit_usage(user: User, db_client: Prisma | None = None) -> dict:
    """
    Get current credit usage information for a user.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Dictionary with credit usage information
    """
    if db_client is None:
        db_client = db

    # Ensure credit period is active
    user = await ensure_credit_period(user, db_client)

    # Reset daily credits if needed (for FREE tier)
    user = await reset_daily_credits_if_needed(user, db_client)

    # Refresh user from database
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    tier_str = str(user.tier) if user.tier else "FREE"
    credits_used = user.creditsUsed or 0
    hard_cap = user.creditsHardCap or 0
    soft_cap = user.creditsSoftCap or 0

    usage_percentage = (credits_used / hard_cap * 100) if hard_cap > 0 else 0
    soft_cap_percentage = (soft_cap / hard_cap * 100) if hard_cap > 0 else 0

    result = {
        "credits_used": credits_used,
        "credits_remaining": max(0, hard_cap - credits_used),
        "hard_cap": hard_cap,
        "soft_cap": soft_cap,
        "usage_percentage": round(usage_percentage, 2),
        "soft_cap_percentage": round(soft_cap_percentage, 2),
        "period_start": user.creditsPeriodStart.isoformat() if user.creditsPeriodStart else None,
        "period_end": user.creditsPeriodEnd.isoformat() if user.creditsPeriodEnd else None,
        "is_soft_cap_reached": soft_cap > 0 and credits_used >= soft_cap,
        "is_hard_cap_reached": hard_cap > 0 and credits_used >= hard_cap,
    }

    # Add daily usage info for FREE tier
    if tier_str == "FREE":
        credits_used_today = user.creditsUsedToday or 0
        daily_limit = user.creditsDailyLimit or 0
        daily_usage_percentage = (credits_used_today / daily_limit * 100) if daily_limit > 0 else 0
        result.update(
            {
                "credits_used_today": credits_used_today,
                "credits_remaining_today": max(0, daily_limit - credits_used_today),
                "daily_limit": daily_limit,
                "daily_usage_percentage": round(daily_usage_percentage, 2),
                "is_daily_limit_reached": daily_limit > 0 and credits_used_today >= daily_limit,
                "next_daily_reset": (
                    (datetime.utcnow() + timedelta(days=1))
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .isoformat()
                ),
            }
        )

    return result


async def reset_credits_for_period_start(
    user: User, period_start: datetime, period_end: datetime, db_client: Prisma | None = None
) -> User:
    """
    Reset credits when a new subscription period starts.

    Args:
        user: User model instance
        period_start: Start of new period
        period_end: End of new period
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object
    """
    if db_client is None:
        db_client = db

    logger.info(
        f"Resetting credits for user {user.id} - new period: {period_start} to {period_end}"
    )

    return await initialize_user_credits(user, period_start, period_end)
