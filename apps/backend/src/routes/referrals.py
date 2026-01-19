"""
Referral routes for managing referral codes and rewards.

This module handles:
- Getting referral code
- Claiming referral rewards
- Getting referral statistics

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..dependencies import CurrentUser
from ..services.referral_service import (
    claim_referral_reward,
    get_claimable_rewards,
    get_referral_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["referrals"])


class ReferralStatsResponse(BaseModel):
    """Response model for referral statistics."""

    referralCode: str
    totalReferrals: int
    claimedRewards: int
    unclaimedRewards: int
    totalTokensEarned: int
    totalTokensClaimed: int


class ClaimableRewardResponse(BaseModel):
    """Response model for claimable reward."""

    id: str
    rewardType: str
    tokens: int
    referredUser: dict
    createdAt: str | None


class ClaimRewardRequest(BaseModel):
    """Request model for claiming a reward."""

    rewardId: str


class ClaimRewardResponse(BaseModel):
    """Response model for claiming a reward."""

    rewardId: str
    tokensClaimed: int
    claimDate: str
    dailyLimitIncrease: int


@router.get("/stats", response_model=ReferralStatsResponse)
async def get_referral_statistics(current_user: CurrentUser):
    """
    Get referral statistics for the current user.

    Args:
        current_user: Current authenticated user

    Returns:
        Referral statistics
    """
    try:
        stats = await get_referral_stats(current_user)
        return ReferralStatsResponse(**stats)
    except Exception as e:
        logger.error(f"Error getting referral stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get referral statistics",
        )


@router.get("/claimable", response_model=list[ClaimableRewardResponse])
async def get_claimable_referral_rewards(current_user: CurrentUser):
    """
    Get all claimable referral rewards for the current user.

    Args:
        current_user: Current authenticated user

    Returns:
        List of claimable rewards
    """
    try:
        rewards = await get_claimable_rewards(current_user)
        return [ClaimableRewardResponse(**reward) for reward in rewards]
    except Exception as e:
        logger.error(f"Error getting claimable rewards: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get claimable rewards",
        )


@router.post("/claim", response_model=ClaimRewardResponse)
async def claim_reward(request: ClaimRewardRequest, current_user: CurrentUser):
    """
    Claim a referral reward. This increases the user's daily limit for today.

    Args:
        request: Claim reward request with reward ID
        current_user: Current authenticated user

    Returns:
        Claim details
    """
    try:
        result = await claim_referral_reward(current_user, request.rewardId)
        return ClaimRewardResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error claiming reward: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim reward",
        )
