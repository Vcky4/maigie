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

**Admin balance adjustment.** ``admin_adjust_balance`` moved a figure in
``User.purchasedCreditsBalance``, and Phase 3 dropped that column with the rest of the
credit meter. There is no balance to adjust: usage is a window that refills on its own
schedule, so the support action a learner actually needs is a pass granted to their
account, which arrives with the pass rails. Restoring this against the window would mean
letting support hand out an allowance that expires in under five hours — a gesture that
looks like help and is spent before the ticket closes.

What remains is history: it describes transactions that really happened, and it is retained.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_purchase_history(
    *, user_id: str, page: int = 1, page_size: int = 20
) -> dict[str, Any]:
    """Get paginated purchase history."""
    from src.domains.billing.services.credit_purchase_service import (
        get_purchase_history as _history,
    )

    return await _history(user_id=user_id, page=page, page_size=page_size)
