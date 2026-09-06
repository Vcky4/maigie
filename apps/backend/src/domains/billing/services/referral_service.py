"""Thin facade over `referral_rewards_service`, kept because the routes import this name.

Three of the four functions that were here are gone with the rewards they described.
`get_claimable_rewards` and `claim_reward` delegated to a claim mechanism that raised a
`daily credit limit` — a column Phase 3 dropped — and `get_daily_limit_increase` was a stub that
returned 0 behind a `TODO: migrate implementation from services/referral_service`, in the file named
`services/referral_service`. It pointed at itself, which is what a stub becomes once the thing it was
a placeholder for has been reconsidered rather than written.

Points replace all of it (Decision O), and their redemption is not a claim against a daily limit but
a purchase of a pass, so it belongs with the pass rails rather than here.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_referral_stats(user_id: str) -> dict[str, Any]:
    """Get referral statistics for a learner."""
    from src.domains.billing.services.referral_rewards_service import (
        get_referral_stats as _stats,
    )

    return await _stats(user_id)
