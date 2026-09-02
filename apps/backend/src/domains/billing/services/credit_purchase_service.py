"""Purchase history for the credit packs that were sold before packs were withdrawn.

**What this module was, and why almost all of it is gone.** It sold credit packs: a catalogue with
currency-aware pricing, a Stripe Checkout session, a Paystack charge, an idempotent webhook
fulfilment that incremented `User.purchasedCreditsBalance`, a receipt email, a push notification, and
an admin tool for adjusting that balance by hand.

Credit packs were withdrawn from the catalogue in Phase 1 (§6.1): a pack of credits cannot be priced
honestly once the thing a credit buys has been replaced by a rolling usage window, and buying
capacity you might not use is a worse deal than buying five hours you will. Phase 3 then dropped
`purchasedCreditsBalance` itself, which left every function here either selling a product that no
longer exists or writing to a column that no longer exists.

So they are deleted rather than disabled: `get_credit_packs`, `initiate_purchase`,
`fulfill_purchase`, `admin_adjust_balance`, `_create_stripe_checkout_session`,
`_create_paystack_charge` and `_send_purchase_receipt_email`. Nothing called any of them —
`paystack_service` dropped its `fulfill_purchase` call in Phase 2b, and the routes went with the
product in Phase 1. The purchase rails that replace them sell **passes** and record a `PlusPurchase`
(Decision G, Decision H); they are a different contract, not a port, which is why there is nothing
here to adapt.

**`get_purchase_history` stays.** It describes transactions that really happened. There happen to be
none — `scripts/count_legacy_commercial_state.py` found zero completed rows — but a support surface
that can answer "what did I pay you" must not start lying the moment a product is retired. Decision H
drops `CreditPurchaseTransaction` in Phase 5, and this goes with the table.

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
    for txn in transactions:
        pack_name = txn.credit_pack.name if txn.credit_pack else "Unknown Pack"
        items.append(
            {
                "id": txn.id,
                "creditPackId": txn.credit_pack_id,
                "creditPackName": pack_name,
                "creditsGranted": txn.credits_granted,
                "amountPaid": txn.amount_paid,
                "currency": txn.currency,
                "priceFormatted": _format_price(txn.amount_paid, txn.currency),
                "status": txn.status,
                "completedAt": (txn.completed_at.isoformat() if txn.completed_at else None),
                "createdAt": txn.created_at.isoformat(),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
    }
