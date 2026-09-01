"""
Credit management service.

Handles purchase history and admin adjustments.

Two things this module used to do are gone.

**Credit packs.** ``get_credit_packs`` and ``initiate_purchase`` sold a quantity of a
unit that is being replaced by a usage window. A pack of credits cannot be priced
honestly once the thing a credit buys has changed, and there is no migration to write
because nobody ever bought one — the router that served these was never mounted.

**Rewarded ads.** ``get_ad_stats`` and ``claim_ad_reward`` granted a daily credit-limit
increase for watching a video. That reward is invisible: a learner cannot see it, predict
it, or plan a study session around it, so it bought no advocacy and cost real inference.
Earning now produces points, and points buy passes — something a learner can hold, see
and choose when to spend. ``AdRewardClaim`` and the two repository methods that write it
are left in place, unread, so a future redesign is not foreclosed.

What remains is history and support tooling: both describe transactions that really
happened, and both are retained.
"""

import logging
from typing import Any

from src.domains.identity.db_models import User

logger = logging.getLogger(__name__)


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
    from src.domains.billing.services.credit_purchase_service import (
        admin_adjust_balance as _adjust,
    )

    return await _adjust(
        admin_id=admin_id,
        target_user_id=target_user_id,
        amount=amount,
        reason=reason,
    )
