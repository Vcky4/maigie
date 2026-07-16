"""
Referral rewards service.

Handles referral statistics, claimable rewards, and reward claiming.
"""

import logging
from typing import Any

from src.domains.identity.db_models import User

from ..repository import billing_repo

logger = logging.getLogger(__name__)


async def get_referral_stats(user: User) -> dict[str, Any]:
    """Get referral statistics for a user."""
    from src.domains.billing.services.referral_rewards_service import get_referral_stats as _stats

    return await _stats(user)


async def get_claimable_rewards(user: User) -> list[dict[str, Any]]:
    """Get all claimable referral rewards."""
    from src.domains.billing.services.referral_rewards_service import (
        get_claimable_rewards as _claimable,
    )

    return await _claimable(user)


async def claim_reward(user: User, reward_id: str) -> dict[str, Any]:
    """Claim a referral reward (increases daily credit limit)."""
    from src.domains.billing.services.referral_rewards_service import (
        claim_referral_reward as _claim,
    )

    return await _claim(user, reward_id)
