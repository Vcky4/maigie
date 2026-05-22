"""
Ad reward routes for managing rewarded video ad credits.

Users watch rewarded video ads (AdMob) and earn credits that increase
their daily usage limit. Limited to a configurable number per day.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..dependencies import CurrentUser
from ..core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ads"])

# Configuration
AD_REWARD_CREDITS = 500  # Credits per ad watched
MAX_ADS_PER_DAY = 10  # Maximum ads a user can watch per day


class AdRewardRequest(BaseModel):
    """Request model for claiming an ad reward."""

    adType: str  # e.g. "rewarded_video"
    rewardAmount: int  # Expected reward amount (for validation)
    adUnitId: str | None = None  # Optional: ad unit ID for verification


class AdRewardResponse(BaseModel):
    """Response model for ad reward claim."""

    credited: int
    adsWatchedToday: int
    remainingToday: int
    dailyLimitIncrease: int


class AdStatsResponse(BaseModel):
    """Response model for ad watch statistics."""

    adsWatchedToday: int
    maxPerDay: int
    remainingToday: int
    creditsPerAd: int
    totalEarned: int


@router.get("/stats", response_model=AdStatsResponse)
async def get_ad_stats(current_user: CurrentUser):
    """Get ad watch statistics for the current user."""
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Count ads watched today
        ads_today = await db.adrewardclaim.count(
            where={
                "userId": current_user.id,
                "createdAt": {"gte": today_start},
            }
        )

        # Total credits earned from ads
        all_claims = await db.adrewardclaim.find_many(where={"userId": current_user.id})
        total_earned = sum(claim.credits for claim in all_claims)

        return AdStatsResponse(
            adsWatchedToday=ads_today,
            maxPerDay=MAX_ADS_PER_DAY,
            remainingToday=max(0, MAX_ADS_PER_DAY - ads_today),
            creditsPerAd=AD_REWARD_CREDITS,
            totalEarned=total_earned,
        )
    except Exception as e:
        logger.error(f"Error getting ad stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ad statistics",
        )


@router.post("/reward", response_model=AdRewardResponse)
async def claim_ad_reward(request: AdRewardRequest, current_user: CurrentUser):
    """
    Claim credits for watching a rewarded ad.

    Validates that the user hasn't exceeded the daily limit,
    then credits them and records the claim.
    """
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Check daily limit
        ads_today = await db.adrewardclaim.count(
            where={
                "userId": current_user.id,
                "createdAt": {"gte": today_start},
            }
        )

        if ads_today >= MAX_ADS_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily ad limit reached ({MAX_ADS_PER_DAY} per day)",
            )

        # Validate reward amount matches expected
        credits = AD_REWARD_CREDITS
        if request.rewardAmount != credits:
            logger.warning(
                f"Ad reward amount mismatch: expected {credits}, got {request.rewardAmount} "
                f"for user {current_user.id}"
            )
            # Still credit the standard amount (don't trust client-sent amount)

        # Record the ad reward claim
        await db.adrewardclaim.create(
            data={
                "userId": current_user.id,
                "adType": request.adType,
                "credits": credits,
                "adUnitId": request.adUnitId,
            }
        )

        ads_watched = ads_today + 1

        logger.info(
            f"User {current_user.id} earned {credits} credits from ad "
            f"({ads_watched}/{MAX_ADS_PER_DAY} today)"
        )

        return AdRewardResponse(
            credited=credits,
            adsWatchedToday=ads_watched,
            remainingToday=max(0, MAX_ADS_PER_DAY - ads_watched),
            dailyLimitIncrease=credits,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error claiming ad reward: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim ad reward",
        )
