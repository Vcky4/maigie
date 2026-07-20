"""
Credit purchase notification utilities.

Handles formatting of push notifications and email notifications
for credit pack purchases.
"""

from typing import Any

CREDIT_PURCHASE_NOTIFICATION_TITLE = "Credits Purchased"


class CreditPurchaseNotificationData:
    """Data for a credit purchase notification."""

    def __init__(self, **kwargs):
        self.amount = kwargs.get("amount", 0)
        self.credits_granted = kwargs.get("credits_granted", 0)
        self.pack_name = kwargs.get("pack_name", "")
        self.new_balance = kwargs.get("new_balance", 0)
        self.amount_paid = kwargs.get("amount_paid", 0)
        self.currency = kwargs.get("currency", "USD")
        self.user_id = kwargs.get("user_id", "")
        self.user_email = kwargs.get("user_email", None)
        self.user_name = kwargs.get("user_name", None)


def format_push_notification_body(data: CreditPurchaseNotificationData) -> str:
    """Format the push notification body for a credit purchase."""
    return f"You purchased {data.pack_name} — {data.credits_granted:,} credits added!"


def format_push_notification_payload(data: CreditPurchaseNotificationData) -> dict[str, Any]:
    """Format the push notification payload for a credit purchase."""
    return {
        "type": "credit_purchase",
        "credits_granted": data.credits_granted,
        "pack_name": data.pack_name,
        "new_balance": data.new_balance,
    }


def format_email_subject(pack_name: str, credits_granted: int) -> str:
    """Format the email subject line for a purchase receipt."""
    return f"Purchase Confirmed: {pack_name} ({credits_granted:,} credits)"


def get_email_template_data(data: CreditPurchaseNotificationData) -> dict[str, Any]:
    """Get template data for purchase receipt email."""
    from src.domains.billing.services.credit_purchase_service import _format_price

    return {
        "credits_granted": f"{data.credits_granted:,}",
        "pack_name": data.pack_name,
        "new_balance": f"{data.new_balance:,}",
        "price_formatted": _format_price(data.amount_paid, data.currency),
        "currency": data.currency,
    }
