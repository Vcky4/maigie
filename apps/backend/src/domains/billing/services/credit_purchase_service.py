"""Purchase history — now over `PlusPurchase`, the passes and subscriptions a learner actually bought.

**What this module was, and why almost all of it is gone.** It sold credit packs: a catalogue with
currency-aware pricing, a Stripe Checkout session, a Paystack charge, an idempotent webhook
fulfilment that incremented `User.purchasedCreditsBalance`, a receipt email, a push notification, and
an admin tool for adjusting that balance by hand.

Credit packs were withdrawn from the catalogue in Phase 1 (§6.1): a pack of credits cannot be priced
honestly once the thing a credit buys has been replaced by a rolling usage window. Phase 3 dropped
`purchasedCreditsBalance`, and Decision H (this change) drops `CreditPurchaseTransaction` and
`CreditPack` themselves — both had zero rows, since nobody ever bought a pack.

So the selling functions are long deleted, and `get_purchase_history` no longer reads the credit
tables. It reads `PlusPurchase`, which is the record of every pass and subscription purchase
(Decision G). The module name is now a fossil — it predates passes — but renaming it is a bigger diff
than it earns, so it stays until something else touches it.

**Why a support surface rather than a receipts list.** The question this answers is "what did I pay
you", so it lists every purchase whatever its status: a `failed` or `refunded` row is exactly what a
learner asking that question needs to see, and hiding them is how a support tool becomes an argument.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import math
from typing import Any

from src.domains.billing.repository import billing_repo
from src.shared.exceptions import ValidationError

logger = logging.getLogger(__name__)


def _format_price(amount_smallest_unit: int, currency: str) -> str:
    """Format a price in the smallest currency unit as a display string.

    Args:
        amount_smallest_unit: Amount in cents (USD) or kobo (NGN).
        currency: Currency code ("USD" or "NGN").

    Returns:
        Formatted price string, e.g. "$1.99" or "₦3,200".
    """
    if currency == "NGN":
        naira = amount_smallest_unit // 100
        return f"₦{naira:,}"
    dollars = amount_smallest_unit / 100
    if dollars == int(dollars):
        return f"${int(dollars)}"
    return f"${dollars:.2f}"


async def get_purchase_history(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    db_client: Any | None = None,
) -> dict:
    """Return paginated purchase transaction history for a user.

    Args:
        user_id: The user's ID.
        page: Page number (1-indexed). Defaults to 1.
        page_size: Number of items per page. Must be between 1 and 100.
            Defaults to 20.
        db_client: Optional (kept for backward compat, ignored).

    Returns:
        Dict with items, total, page, pageSize, and totalPages.

    Raises:
        ValidationError: If page_size is out of range.
    """
    if page_size < 1 or page_size > 100:
        raise ValidationError(
            message="page_size must be between 1 and 100",
            detail=f"Received page_size={page_size}",
        )

    if page < 1:
        page = 1

    skip = (page - 1) * page_size

    transactions, total = await billing_repo.get_purchase_history(
        user_id, skip=skip, take=page_size
    )

    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = []
    for purchase in transactions:
        items.append(
            {
                "id": purchase.id,
                "productId": purchase.product_id,
                # `pass` | `subscription`. What was bought, not what it granted — a pass and the
                # subscription are different lines on a receipt even when they cost the same.
                "productKind": purchase.product_kind,
                "provider": purchase.provider,
                "amountMinor": purchase.amount_minor,
                "currency": purchase.currency,
                "priceFormatted": _format_price(purchase.amount_minor, purchase.currency),
                "status": purchase.status,
                "completedAt": (
                    purchase.completed_at.isoformat() if purchase.completed_at else None
                ),
                "refundedAt": (purchase.refunded_at.isoformat() if purchase.refunded_at else None),
                "createdAt": purchase.created_at.isoformat(),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
    }
