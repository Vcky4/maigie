"""
Credit management service.

Handles credit pack catalog, purchase initiation, purchase history,
admin adjustments, and ad rewards.
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Any

from src.domains.identity.db_models import User
from src.shared.events import BillingEvents, emit
from src.shared.exceptions import NotFoundError, ValidationError

from ..repository import billing_repo

logger = logging.getLogger(__name__)

# Ad reward configuration
AD_REWARD_CREDITS = 500
MAX_ADS_PER_DAY = 10


async def get_credit_packs(user: User) -> list[dict[str, Any]]:
    """Get available credit packs with user-specific pricing."""
    from src.domains.billing.services.credit_purchase_service import get_credit_packs as _get_packs

    return await _get_packs(user)


async def initiate_purchase(
    *, user: User, pack_id: str, success_url: str, cancel_url: str
) -> dict[str, Any]:
    """Initiate a credit pack purchase (one-time payment)."""
    from src.domains.billing.services.credit_purchase_service import initiate_purchase as _initiate

    return await _initiate(
        user=user,
        pack_id=pack_id,
        success_url=success_url,
        cancel_url=cancel_url,
    )


async def get_purchase_history(
    *, user_id: str, page: int = 1, page_size: int = 20
) -> dict[str, Any]:
    """Get paginated purchase history."""
    from src.domains.billing.services.credit_purchase_service import (
        get_purchase_history as _history,
    )

    return await _history(user_id=user_id, page=page, page_size=page_size)


async def admin_adjust_balance(
    *, admin_id: str, target_user_id: str, amount: int, reason: str
) -> User:
    """Admin: adjust a user's purchased credits balance."""
    from src.domains.billing.services.credit_purchase_service import admin_adjust_balance as _adjust

    return await _adjust(
        admin_id=admin_id,
        target_user_id=target_user_id,
        amount=amount,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Ad Rewards
# ---------------------------------------------------------------------------


async def get_ad_stats(user_id: str) -> dict[str, Any]:
    """Get ad watch statistics for a user."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    ads_today = await billing_repo.count_ads_today(user_id, today_start)
    total_earned = await billing_repo.get_total_ad_earnings(user_id)

    return {
        "adsWatchedToday": ads_today,
        "maxPerDay": MAX_ADS_PER_DAY,
        "remainingToday": max(0, MAX_ADS_PER_DAY - ads_today),
        "creditsPerAd": AD_REWARD_CREDITS,
        "totalEarned": total_earned,
    }


async def claim_ad_reward(
    *, user_id: str, ad_type: str, ad_unit_id: str | None = None
) -> dict[str, Any]:
    """Claim credits for watching a rewarded ad."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    ads_today = await billing_repo.count_ads_today(user_id, today_start)
    if ads_today >= MAX_ADS_PER_DAY:
        raise ValidationError(f"Daily ad limit reached ({MAX_ADS_PER_DAY} per day)")

    credits = AD_REWARD_CREDITS

    await billing_repo.create_ad_claim(
        {
            "userId": user_id,
            "adType": ad_type,
            "credits": credits,
            "adUnitId": ad_unit_id,
        }
    )

    ads_watched = ads_today + 1
    logger.info(
        f"User {user_id} earned {credits} credits from ad ({ads_watched}/{MAX_ADS_PER_DAY})"
    )

    await emit(
        BillingEvents.CREDITS_PURCHASED, {"user_id": user_id, "credits": credits, "source": "ad"}
    )

    return {
        "credited": credits,
        "adsWatchedToday": ads_watched,
        "remainingToday": max(0, MAX_ADS_PER_DAY - ads_watched),
        "dailyLimitIncrease": credits,
    }
