"""
Referral service for managing referral rewards.

This module handles:
- Referral code generation
- Referral tracking
- Reward claiming
- Daily limit increases from claimed rewards

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from prisma import Prisma
from prisma.models import User

from ..core.database import db

logger = logging.getLogger(__name__)

# Referral reward amounts (in tokens)
REFERRAL_REWARDS = {
    "signup": 1000,  # 1000 tokens for referring a user who signs up
    "subscription": 500,  # 500 tokens for referring a user who subscribes
}


def generate_referral_code(length: int = 8) -> str:
    """
    Generate a unique referral code.

    Args:
        length: Length of the referral code (default: 8)

    Returns:
        Unique referral code string
    """
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def get_or_create_referral_code(user: User, db_client: Prisma | None = None) -> str:
    """
    Get existing referral code for user or generate a new one.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Referral code string
    """
    if db_client is None:
        db_client = db

    if user.referralCode:
        return user.referralCode

    # Generate a unique code
    max_attempts = 10
    for _ in range(max_attempts):
        code = generate_referral_code()
        # Check if code already exists
        existing_user = await db_client.user.find_unique(where={"referralCode": code})
        if not existing_user:
            # Update user with new referral code
            updated_user = await db_client.user.update(
                where={"id": user.id},
                data={"referralCode": code},
            )
            logger.info(f"Generated referral code {code} for user {user.id}")
            return code

    raise ValueError("Failed to generate unique referral code after multiple attempts")


async def track_referral_signup(
    referred_user: User, referral_code: str, db_client: Prisma | None = None
) -> Optional[User]:
    """
    Track when a user signs up with a referral code and award the referrer.

    Args:
        referred_user: User who signed up
        referral_code: Referral code used during signup
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Referrer User object if found, None otherwise
    """
    if db_client is None:
        db_client = db

    # Find referrer by code
    referrer = await db_client.user.find_unique(where={"referralCode": referral_code})
    if not referrer:
        logger.warning(f"Referral code {referral_code} not found")
        return None

    # Prevent self-referral
    if referrer.id == referred_user.id:
        logger.warning(f"User {referred_user.id} attempted self-referral")
        return None

    # Check if reward already exists
    existing_reward = await db_client.referralreward.find_first(
        where={
            "referrerId": referrer.id,
            "referredUserId": referred_user.id,
            "rewardType": "signup",
        }
    )

    if existing_reward:
        logger.info(
            f"Signup reward already exists for referrer {referrer.id} and referred {referred_user.id}"
        )
        return referrer

    # Create referral reward
    await db_client.referralreward.create(
        data={
            "referrerId": referrer.id,
            "referredUserId": referred_user.id,
            "rewardType": "signup",
            "tokens": REFERRAL_REWARDS["signup"],
            "isClaimed": False,
        }
    )

    # Update referred user with referral code used
    await db_client.user.update(
        where={"id": referred_user.id},
        data={"referredByCode": referral_code},
    )

    logger.info(
        f"Created signup referral reward: referrer {referrer.id} -> referred {referred_user.id}"
    )

    return referrer


async def track_referral_subscription(referred_user: User, db_client: Prisma | None = None) -> None:
    """
    Track when a referred user subscribes and award the referrer.

    Args:
        referred_user: User who subscribed (must have referredByCode set)
        db_client: Optional Prisma client (defaults to global db)
    """
    if db_client is None:
        db_client = db

    if not referred_user.referredByCode:
        # User wasn't referred, nothing to do
        return

    # Find referrer by code
    referrer = await db_client.user.find_unique(
        where={"referralCode": referred_user.referredByCode}
    )
    if not referrer:
        logger.warning(f"Referrer not found for code {referred_user.referredByCode}")
        return

    # Check if subscription reward already exists
    existing_reward = await db_client.referralreward.find_first(
        where={
            "referrerId": referrer.id,
            "referredUserId": referred_user.id,
            "rewardType": "subscription",
        }
    )

    if existing_reward:
        logger.info(
            f"Subscription reward already exists for referrer {referrer.id} and referred {referred_user.id}"
        )
        return

    # Create subscription referral reward
    await db_client.referralreward.create(
        data={
            "referrerId": referrer.id,
            "referredUserId": referred_user.id,
            "rewardType": "subscription",
            "tokens": REFERRAL_REWARDS["subscription"],
            "isClaimed": False,
        }
    )

    logger.info(
        f"Created subscription referral reward: referrer {referrer.id} -> referred {referred_user.id}"
    )


async def get_claimable_rewards(user: User, db_client: Prisma | None = None) -> list[dict]:
    """
    Get all unclaimed referral rewards for a user.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        List of claimable reward dictionaries
    """
    if db_client is None:
        db_client = db

    rewards = await db_client.referralreward.find_many(
        where={"referrerId": user.id, "isClaimed": False},
        include={"referredUser": True},
        order={"createdAt": "desc"},
    )

    return [
        {
            "id": reward.id,
            "rewardType": reward.rewardType,
            "tokens": reward.tokens,
            "referredUser": {
                "id": reward.referredUser.id,
                "email": reward.referredUser.email,
                "name": reward.referredUser.name,
            },
            "createdAt": reward.createdAt.isoformat() if reward.createdAt else None,
        }
        for reward in rewards
    ]


async def claim_referral_reward(
    user: User, reward_id: str, db_client: Prisma | None = None
) -> dict:
    """
    Claim a referral reward. This increases the user's daily limit for the current day.

    Args:
        user: User model instance
        reward_id: ID of the reward to claim
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Dictionary with claim details
    """
    if db_client is None:
        db_client = db

    # Get the reward
    reward = await db_client.referralreward.find_unique(where={"id": reward_id})
    if not reward:
        raise ValueError("Reward not found")

    # Verify ownership
    if reward.referrerId != user.id:
        raise ValueError("Reward does not belong to this user")

    # Check if already claimed
    if reward.isClaimed:
        raise ValueError("Reward already claimed")

    # Get today's date (midnight UTC)
    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Check if user has already claimed a reward today
    # If so, we need to check if this reward was already claimed today
    existing_claim = await db_client.referralrewardclaim.find_first(
        where={
            "userId": user.id,
            "claimDate": {"gte": today_midnight},
            "rewardId": reward_id,
        }
    )

    if existing_claim:
        raise ValueError("This reward has already been claimed today")

    # Mark reward as claimed
    await db_client.referralreward.update(
        where={"id": reward_id},
        data={
            "isClaimed": True,
            "claimedAt": now,
            "claimDate": today_midnight,
        },
    )

    # Create claim record
    claim = await db_client.referralrewardclaim.create(
        data={
            "userId": user.id,
            "rewardId": reward_id,
            "tokensClaimed": reward.tokens,
            "claimDate": today_midnight,
            "dailyLimitIncrease": reward.tokens,
        }
    )

    # Increase user's daily limit for today
    # We'll track this separately - the credit service will check claimed rewards
    # For now, we'll store it in a way that credit_service can access it

    logger.info(f"User {user.id} claimed referral reward {reward_id}: {reward.tokens} tokens")

    return {
        "rewardId": reward_id,
        "tokensClaimed": reward.tokens,
        "claimDate": today_midnight.isoformat(),
        "dailyLimitIncrease": reward.tokens,
    }


async def get_daily_limit_increase(user: User, db_client: Prisma | None = None) -> int:
    """
    Get the total daily limit increase from claimed referral rewards for today.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Total daily limit increase in tokens
    """
    if db_client is None:
        db_client = db

    # Get today's date (midnight UTC)
    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Get all claims for today
    claims = await db_client.referralrewardclaim.find_many(
        where={
            "userId": user.id,
            "claimDate": {"gte": today_midnight},
        }
    )

    # Sum up the daily limit increases
    total_increase = sum(claim.dailyLimitIncrease for claim in claims)

    return total_increase


async def get_referral_stats(user: User, db_client: Prisma | None = None) -> dict:
    """
    Get referral statistics for a user.

    Args:
        user: User model instance
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Dictionary with referral statistics
    """
    if db_client is None:
        db_client = db

    # Get referral code
    referral_code = await get_or_create_referral_code(user, db_client)

    # Count total referrals
    total_referrals = await db_client.referralreward.count(where={"referrerId": user.id})

    # Count claimed rewards
    claimed_rewards = await db_client.referralreward.count(
        where={"referrerId": user.id, "isClaimed": True}
    )

    # Count unclaimed rewards
    unclaimed_rewards = await db_client.referralreward.count(
        where={"referrerId": user.id, "isClaimed": False}
    )

    # Calculate total tokens earned
    all_rewards = await db_client.referralreward.find_many(where={"referrerId": user.id})
    total_tokens_earned = sum(reward.tokens for reward in all_rewards)

    # Calculate total tokens claimed
    claimed_reward_records = await db_client.referralreward.find_many(
        where={"referrerId": user.id, "isClaimed": True}
    )
    total_tokens_claimed = sum(reward.tokens for reward in claimed_reward_records)

    return {
        "referralCode": referral_code,
        "totalReferrals": total_referrals,
        "claimedRewards": claimed_rewards,
        "unclaimedRewards": unclaimed_rewards,
        "totalTokensEarned": total_tokens_earned,
        "totalTokensClaimed": total_tokens_claimed,
    }
