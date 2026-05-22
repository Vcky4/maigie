"""
Credit Purchase Service for managing credit pack purchases.

This module handles:
- Credit pack catalog retrieval with currency-aware pricing
- Purchase session creation (Stripe/Paystack)
- Idempotent purchase fulfillment from webhook events
- Purchase history retrieval with pagination
- Admin credit balance adjustments

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import math
from datetime import UTC, datetime, timedelta

import httpx
import stripe
from prisma import Prisma
from prisma.models import User

from src.config import get_settings
from src.core.database import db
from src.services.audit_service import log_admin_action
from src.services.credit_purchase_notifications import (
    CREDIT_PURCHASE_NOTIFICATION_TITLE,
    CreditPurchaseNotificationData,
    format_push_notification_body,
    format_push_notification_payload,
)
from src.services.push_notification_service import send_push_notification
from src.utils.exceptions import ResourceNotFoundError, ValidationError

logger = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"


def _format_price(amount_smallest_unit: int, currency: str) -> str:
    """Format a price in smallest currency unit to a display string.

    Args:
        amount_smallest_unit: Amount in cents (USD) or kobo (NGN).
        currency: Currency code ("USD" or "NGN").

    Returns:
        Formatted price string, e.g. "$1.99" or "₦3,200".
    """
    if currency == "NGN":
        # Convert kobo to naira
        naira = amount_smallest_unit // 100
        return f"₦{naira:,}"
    else:
        # Convert cents to dollars
        dollars = amount_smallest_unit / 100
        # Format without trailing zeros for whole numbers
        if dollars == int(dollars):
            return f"${int(dollars)}"
        return f"${dollars:.2f}"


async def get_credit_packs(user: User, db_client: Prisma | None = None) -> list[dict]:
    """Return the credit pack catalog with prices in the user's currency.

    Fetches active credit packs ordered by sortOrder. Determines the user's
    payment provider to select the appropriate currency (NGN for Paystack,
    USD for Stripe or default).

    Args:
        user: The authenticated user requesting the catalog.
        db_client: Optional Prisma client (defaults to global db).

    Returns:
        List of credit pack dicts with pricing in the user's currency.
    """
    if db_client is None:
        db_client = db

    # Fetch active packs ordered by sortOrder (lowest to highest)
    packs = await db_client.creditpack.find_many(
        where={"isActive": True},
        order={"sortOrder": "asc"},
    )

    # Determine currency based on user's payment provider
    payment_provider = getattr(user, "paymentProvider", None)
    if payment_provider == "paystack":
        currency = "NGN"
    else:
        # Stripe or no provider defaults to USD
        currency = "USD"

    result = []
    for pack in packs:
        if currency == "NGN":
            price = pack.priceNgnKobo
        else:
            price = pack.priceUsdCents

        total_credits = pack.credits + pack.bonusCredits

        result.append(
            {
                "id": pack.id,
                "name": pack.name,
                "credits": pack.credits,
                "bonusCredits": pack.bonusCredits,
                "totalCredits": total_credits,
                "price": price,
                "currency": currency,
                "priceFormatted": _format_price(price, currency),
            }
        )

    return result


async def initiate_purchase(
    user: User,
    pack_id: str,
    success_url: str,
    cancel_url: str,
    db_client: Prisma | None = None,
) -> dict:
    """Create a one-time payment session for a credit pack purchase.

    Validates the pack exists and is active, creates a pending transaction,
    then creates a Stripe Checkout Session or Paystack transaction initialization.

    Args:
        user: The authenticated user making the purchase.
        pack_id: ID of the credit pack to purchase.
        success_url: URL to redirect after successful payment.
        cancel_url: URL to redirect if payment is cancelled.
        db_client: Optional Prisma client (defaults to global db).

    Returns:
        Dict with sessionUrl, sessionId, and expiresAt.

    Raises:
        ResourceNotFoundError: If pack_id doesn't exist or is inactive.
        ValueError: If payment provider is not configured.
    """
    if db_client is None:
        db_client = db

    settings = get_settings()

    # Validate the credit pack exists and is active
    pack = await db_client.creditpack.find_first(where={"id": pack_id, "isActive": True})
    if not pack:
        raise ResourceNotFoundError("CreditPack", pack_id)

    # Determine payment provider and currency
    payment_provider = getattr(user, "paymentProvider", None) or "stripe"
    if payment_provider == "paystack":
        currency = "NGN"
        amount = pack.priceNgnKobo
    else:
        currency = "USD"
        amount = pack.priceUsdCents

    total_credits = pack.credits + pack.bonusCredits
    expires_at = datetime.now(UTC) + timedelta(minutes=30)

    if payment_provider == "stripe":
        # Create Stripe Checkout Session in payment mode (one-time)
        session = await _create_stripe_checkout_session(
            user=user,
            pack=pack,
            amount=amount,
            currency=currency,
            total_credits=total_credits,
            success_url=success_url,
            cancel_url=cancel_url,
            expires_at=expires_at,
            settings=settings,
        )
        session_id = session["id"]
        session_url = session["url"]
        provider_reference = session["payment_intent"] or session["id"]
    else:
        # Create Paystack one-time charge initialization
        paystack_result = await _create_paystack_charge(
            user=user,
            pack=pack,
            amount=amount,
            total_credits=total_credits,
            success_url=success_url,
            settings=settings,
        )
        session_id = paystack_result["access_code"]
        session_url = paystack_result["authorization_url"]
        provider_reference = paystack_result["reference"]

    # Create pending transaction record
    await db_client.creditpurchasetransaction.create(
        data={
            "userId": user.id,
            "creditPackId": pack.id,
            "creditsGranted": total_credits,
            "amountPaid": amount,
            "currency": currency,
            "paymentProvider": payment_provider,
            "providerReference": provider_reference,
            "sessionId": session_id,
            "sessionExpiresAt": expires_at,
            "status": "pending",
        }
    )

    return {
        "sessionUrl": session_url,
        "sessionId": session_id,
        "expiresAt": expires_at.isoformat(),
    }


async def fulfill_purchase(
    provider_reference: str,
    provider: str,
    db_client: Prisma | None = None,
) -> bool:
    """Idempotently fulfill a credit pack purchase after payment confirmation.

    Checks if the transaction has already been completed (idempotency guard).
    If not, updates the transaction status, increments the user's purchased
    credits balance, and sends a push notification.

    Args:
        provider_reference: The payment provider's unique reference
            (Stripe payment_intent ID or Paystack reference).
        provider: Payment provider name ("stripe" or "paystack").
        db_client: Optional Prisma client (defaults to global db).

    Returns:
        True if credits were granted (first-time fulfillment).
        False if already completed (duplicate webhook).
    """
    if db_client is None:
        db_client = db

    # Find the transaction by provider reference
    transaction = await db_client.creditpurchasetransaction.find_first(
        where={"providerReference": provider_reference},
        include={"creditPack": True},
    )

    if not transaction:
        logger.warning(
            f"No transaction found for provider_reference={provider_reference}, "
            f"provider={provider}. Logging for manual reconciliation.",
            extra={
                "provider_reference": provider_reference,
                "provider": provider,
                "event": "unknown_transaction_webhook",
            },
        )
        return False

    # Idempotency check: if already completed, skip
    if transaction.status == "completed":
        logger.info(
            f"Duplicate fulfillment attempt for transaction {transaction.id} "
            f"(provider_reference={provider_reference}). Skipping."
        )
        return False

    # Update transaction status to completed
    now = datetime.now(UTC)
    await db_client.creditpurchasetransaction.update(
        where={"id": transaction.id},
        data={
            "status": "completed",
            "completedAt": now,
        },
    )

    # Atomically increment user's purchased credits balance
    updated_user = await db_client.user.update(
        where={"id": transaction.userId},
        data={
            "purchasedCreditsBalance": {"increment": transaction.creditsGranted},
        },
    )

    # Emit real-time balance update via WebSocket to all connected clients
    new_balance = updated_user.purchasedCreditsBalance if updated_user else 0
    pack_name = transaction.creditPack.name if transaction.creditPack else "Credit Pack"

    try:
        from src.services.ws_event_bus import publish_ws_event

        await publish_ws_event(
            user_id=transaction.userId,
            payload={
                "type": "credit_balance_update",
                "purchasedCreditsBalance": new_balance,
                "creditsGranted": transaction.creditsGranted,
                "packName": pack_name,
            },
        )
    except Exception as e:
        # Don't fail the fulfillment if WebSocket notification fails
        logger.error(
            f"Failed to emit WebSocket balance update for user {transaction.userId}: {e}",
            exc_info=True,
        )

    # Send purchase confirmation notifications

    notification_data = CreditPurchaseNotificationData(
        credits_granted=transaction.creditsGranted,
        pack_name=pack_name,
        new_balance=new_balance,
        amount_paid=transaction.amountPaid,
        currency=transaction.currency,
        user_id=transaction.userId,
        user_email=getattr(updated_user, "email", None),
        user_name=getattr(updated_user, "name", None),
    )

    # Send push notification
    try:
        await send_push_notification(
            user_id=transaction.userId,
            title=CREDIT_PURCHASE_NOTIFICATION_TITLE,
            body=format_push_notification_body(notification_data),
            data=format_push_notification_payload(notification_data),
        )
    except Exception as e:
        # Don't fail the fulfillment if notification fails
        logger.error(
            f"Failed to send purchase push notification for user {transaction.userId}: {e}",
            exc_info=True,
        )

    # Send optional email receipt
    try:
        await _send_purchase_receipt_email(notification_data)
    except Exception as e:
        # Don't fail the fulfillment if email fails
        logger.error(
            f"Failed to send purchase receipt email for user {transaction.userId}: {e}",
            exc_info=True,
        )

    logger.info(
        f"Fulfilled purchase: transaction={transaction.id}, "
        f"user={transaction.userId}, credits={transaction.creditsGranted}, "
        f"provider={provider}"
    )
    return True


async def get_purchase_history(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    db_client: Prisma | None = None,
) -> dict:
    """Return paginated purchase transaction history for a user.

    Args:
        user_id: The user's ID.
        page: Page number (1-indexed). Defaults to 1.
        page_size: Number of items per page. Must be between 1 and 100.
            Defaults to 20.
        db_client: Optional Prisma client (defaults to global db).

    Returns:
        Dict with items, total, page, pageSize, and totalPages.

    Raises:
        ValidationError: If page_size is out of range.
    """
    if db_client is None:
        db_client = db

    # Validate page_size
    if page_size < 1 or page_size > 100:
        raise ValidationError(
            message="page_size must be between 1 and 100",
            detail=f"Received page_size={page_size}",
        )

    # Ensure page is at least 1
    if page < 1:
        page = 1

    # Get total count
    total = await db_client.creditpurchasetransaction.count(where={"userId": user_id})

    # Calculate pagination
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    skip = (page - 1) * page_size

    # Fetch transactions ordered by createdAt desc
    transactions = await db_client.creditpurchasetransaction.find_many(
        where={"userId": user_id},
        order={"createdAt": "desc"},
        skip=skip,
        take=page_size,
        include={"creditPack": True},
    )

    # Format response items
    items = []
    for txn in transactions:
        pack_name = txn.creditPack.name if txn.creditPack else "Unknown Pack"
        items.append(
            {
                "id": txn.id,
                "creditPackName": pack_name,
                "creditsGranted": txn.creditsGranted,
                "amountPaid": txn.amountPaid,
                "currency": txn.currency,
                "priceFormatted": _format_price(txn.amountPaid, txn.currency),
                "status": txn.status,
                "completedAt": txn.completedAt.isoformat() if txn.completedAt else None,
                "createdAt": txn.createdAt.isoformat(),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
    }


async def admin_adjust_balance(
    admin_id: str,
    target_user_id: str,
    amount: int,
    reason: str,
    db_client: Prisma | None = None,
) -> User:
    """Adjust a user's purchased credits balance (admin action).

    Validates the target user exists and that the adjustment won't result
    in a negative balance. Atomically updates the balance and creates an
    audit log entry.

    Args:
        admin_id: ID of the admin performing the adjustment.
        target_user_id: ID of the user whose balance is being adjusted.
        amount: Adjustment amount (positive = grant, negative = deduct).
        reason: Admin-provided reason for the adjustment.
        db_client: Optional Prisma client (defaults to global db).

    Returns:
        The updated User model.

    Raises:
        ResourceNotFoundError: If target user doesn't exist.
        ValidationError: If adjustment would result in negative balance.
    """
    if db_client is None:
        db_client = db

    # Validate target user exists
    target_user = await db_client.user.find_unique(where={"id": target_user_id})
    if not target_user:
        raise ResourceNotFoundError("User", target_user_id)

    # Validate adjustment won't result in negative balance
    current_balance = target_user.purchasedCreditsBalance or 0
    if amount < 0 and abs(amount) > current_balance:
        raise ValidationError(
            message=(
                f"Cannot deduct {abs(amount)} credits. "
                f"Maximum deductible amount is {current_balance} "
                f"(user's current purchased credits balance)."
            ),
            detail=f"current_balance={current_balance}, requested_deduction={abs(amount)}",
        )

    # Atomically update the balance
    updated_user = await db_client.user.update(
        where={"id": target_user_id},
        data={
            "purchasedCreditsBalance": {"increment": amount},
        },
    )

    # Create audit log entry
    try:
        await log_admin_action(
            admin_user_id=admin_id,
            action="adjust_purchased_credits",
            resource_type="user",
            resource_id=target_user_id,
            details={
                "adjustment_amount": amount,
                "reason": reason,
                "previous_balance": current_balance,
                "new_balance": current_balance + amount,
            },
            db_client=db_client,
        )
    except Exception as e:
        # Log but don't fail the adjustment if audit logging fails
        logger.error(
            f"Failed to create audit log for credit adjustment: {e}",
            exc_info=True,
        )

    logger.info(
        f"Admin {admin_id} adjusted credits for user {target_user_id}: "
        f"amount={amount}, reason='{reason}', "
        f"balance: {current_balance} -> {current_balance + amount}"
    )

    return updated_user


# =============================================================================
# Private helper functions
# =============================================================================


async def _send_purchase_receipt_email(
    data: CreditPurchaseNotificationData,
) -> None:
    """Send a purchase receipt email to the user (best-effort).

    Uses the credit_purchase_receipt email template. Skips silently if
    the user has no email address on file.

    Args:
        data: The notification data containing user and purchase details.
    """
    from src.services.credit_purchase_notifications import (
        format_email_subject,
        get_email_template_data,
    )

    if not data.user_email:
        logger.debug(f"No email for user {data.user_id} — skipping purchase receipt email")
        return

    from src.services import email as email_service

    settings = get_settings()
    template_data = get_email_template_data(data)
    template_data["app_name"] = "Maigie"
    template_data["logo_url"] = settings.EMAIL_LOGO_URL or ""
    template_data["dashboard_url"] = (
        f"{settings.FRONTEND_BASE_URL or 'https://app.maigie.com'}/dashboard"
    )

    subject = format_email_subject(data.pack_name, data.credits_granted)

    await email_service.send_bulk_email(
        email=data.user_email,
        name=data.user_name,
        subject=subject,
        content=(
            f"<p>Your credit purchase was successful.</p>"
            f"<p><strong>Pack:</strong> {data.pack_name}<br>"
            f"<strong>Credits Added:</strong> {template_data['credits_granted']}<br>"
            f"<strong>Amount Paid:</strong> {template_data['price_formatted']}<br>"
            f"<strong>New Balance:</strong> {template_data['new_balance']} credits</p>"
            f"<p>Your purchased credits never expire and will be used automatically "
            f"after your subscription credits are consumed.</p>"
        ),
    )


async def _create_stripe_checkout_session(
    user: User,
    pack,
    amount: int,
    currency: str,
    total_credits: int,
    success_url: str,
    cancel_url: str,
    expires_at: datetime,
    settings,
) -> dict:
    """Create a Stripe Checkout Session for a one-time credit pack payment.

    Args:
        user: The user making the purchase.
        pack: The CreditPack model instance.
        amount: Price in smallest currency unit.
        currency: Currency code (e.g. "USD").
        total_credits: Total credits to be granted.
        success_url: Redirect URL on success.
        cancel_url: Redirect URL on cancel.
        expires_at: Session expiration time.
        settings: Application settings.

    Returns:
        Dict with Stripe session details (id, url, payment_intent).
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY

    # Calculate expires_at as Unix timestamp for Stripe (must be at least 30 min from now)
    expires_at_unix = int(expires_at.timestamp())

    session = stripe.checkout.Session.create(
        customer_email=user.email,
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": currency.lower(),
                    "product_data": {
                        "name": f"{pack.name} - {total_credits:,} Credits",
                        "description": (
                            f"{pack.credits:,} credits"
                            + (f" + {pack.bonusCredits:,} bonus" if pack.bonusCredits > 0 else "")
                        ),
                    },
                    "unit_amount": amount,
                },
                "quantity": 1,
            }
        ],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        expires_at=expires_at_unix,
        metadata={
            "user_id": user.id,
            "credit_pack_id": pack.id,
            "credits_granted": str(total_credits),
            "purchase_type": "credit_pack",
        },
    )

    return {
        "id": session.id,
        "url": session.url,
        "payment_intent": session.payment_intent,
    }


async def _create_paystack_charge(
    user: User,
    pack,
    amount: int,
    total_credits: int,
    success_url: str,
    settings,
) -> dict:
    """Initialize a Paystack one-time charge for a credit pack purchase.

    Args:
        user: The user making the purchase.
        pack: The CreditPack model instance.
        amount: Price in kobo.
        total_credits: Total credits to be granted.
        success_url: Callback URL after payment.
        settings: Application settings.

    Returns:
        Dict with authorization_url, access_code, and reference.

    Raises:
        ValueError: If Paystack is not configured or initialization fails.
    """
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValueError("Paystack is not configured (PAYSTACK_SECRET_KEY missing)")

    payload = {
        "email": user.email,
        "amount": str(amount),
        "callback_url": success_url,
        "metadata": {
            "user_id": user.id,
            "credit_pack_id": pack.id,
            "credits_granted": str(total_credits),
            "purchase_type": "credit_pack",
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
        )

    data = resp.json()
    if not data.get("status"):
        msg = data.get("message", "Paystack initialization failed")
        logger.error(f"Paystack credit pack init failed for user {user.id}: {msg}")
        raise ValueError(msg)

    result = data.get("data", {})
    return {
        "authorization_url": result.get("authorization_url"),
        "access_code": result.get("access_code"),
        "reference": result.get("reference"),
    }
