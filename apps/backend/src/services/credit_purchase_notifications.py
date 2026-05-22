"""
Credit Purchase Notification Templates and Helpers.

Provides consistent message formatting for credit purchase confirmations
across push notifications and email receipts.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =============================================================================
# Push Notification Constants
# =============================================================================

CREDIT_PURCHASE_NOTIFICATION_TITLE = "Credits Added!"
CREDIT_PURCHASE_NOTIFICATION_TYPE = "credit_purchase_completed"


# =============================================================================
# Message Formatting
# =============================================================================


@dataclass
class CreditPurchaseNotificationData:
    """Data needed to format a credit purchase confirmation notification."""

    credits_granted: int
    pack_name: str
    new_balance: int
    amount_paid: int
    currency: str
    user_id: str
    user_email: str | None = None
    user_name: str | None = None


def format_push_notification_body(data: CreditPurchaseNotificationData) -> str:
    """Format the push notification body for a credit purchase confirmation.

    Args:
        data: The notification data.

    Returns:
        Formatted notification body string.
    """
    return (
        f"{data.credits_granted:,} credits from {data.pack_name} "
        f"have been added to your balance. "
        f"New balance: {data.new_balance:,} credits."
    )


def format_push_notification_payload(data: CreditPurchaseNotificationData) -> dict[str, str]:
    """Format the push notification data payload for a credit purchase.

    Args:
        data: The notification data.

    Returns:
        Dict of string key-value pairs for the push notification data field.
    """
    return {
        "type": CREDIT_PURCHASE_NOTIFICATION_TYPE,
        "credits_granted": str(data.credits_granted),
        "new_balance": str(data.new_balance),
    }


def format_price(amount_smallest_unit: int, currency: str) -> str:
    """Format a price in smallest currency unit to a display string.

    Args:
        amount_smallest_unit: Amount in cents (USD) or kobo (NGN).
        currency: Currency code ("USD" or "NGN").

    Returns:
        Formatted price string, e.g. "$1.99" or "₦3,200".
    """
    if currency == "NGN":
        naira = amount_smallest_unit // 100
        return f"₦{naira:,}"
    else:
        dollars = amount_smallest_unit / 100
        if dollars == int(dollars):
            return f"${int(dollars)}"
        return f"${dollars:.2f}"


def format_email_subject(pack_name: str, credits_granted: int) -> str:
    """Format the email subject for a credit purchase receipt.

    Args:
        pack_name: Name of the purchased credit pack.
        credits_granted: Total credits granted.

    Returns:
        Email subject string.
    """
    return f"Receipt: {credits_granted:,} credits added — {pack_name}"


def get_email_template_data(data: CreditPurchaseNotificationData) -> dict:
    """Build template data dict for the credit purchase receipt email.

    Args:
        data: The notification data.

    Returns:
        Dict of template variables for Jinja2 rendering.
    """
    return {
        "name": data.user_name or "there",
        "pack_name": data.pack_name,
        "credits_granted": f"{data.credits_granted:,}",
        "price_formatted": format_price(data.amount_paid, data.currency),
        "new_balance": f"{data.new_balance:,}",
    }
