"""
Google Play Billing service for verifying and managing Android in-app subscriptions.

This service handles:
- Verifying purchase tokens with Google Play Developer API
- Mapping Google Play product IDs to internal tier values
- Acknowledging subscriptions
- Processing Real-Time Developer Notifications (RTDN)
"""

import json
import logging
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..config import get_settings
from ..core.database import db

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def _get_android_publisher_service():
    """Build the Google Play Developer API client using service account credentials."""
    settings = get_settings()

    if settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    elif settings.GOOGLE_PLAY_SERVICE_ACCOUNT_FILE:
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_PLAY_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
    else:
        raise ValueError(
            "Google Play service account not configured. "
            "Set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON or GOOGLE_PLAY_SERVICE_ACCOUNT_FILE."
        )

    return build("androidpublisher", "v3", credentials=credentials, cache_discovery=False)


def _sku_to_tier(product_id: str) -> str:
    """Map a Google Play product ID (SKU) to the internal tier enum."""
    settings = get_settings()
    if product_id == settings.GOOGLE_PLAY_SKU_PLUS_MONTHLY:
        return "PREMIUM_MONTHLY"
    if product_id == settings.GOOGLE_PLAY_SKU_PLUS_YEARLY:
        return "PREMIUM_YEARLY"
    return "FREE"


async def verify_subscription(
    user_id: str,
    product_id: str,
    purchase_token: str,
) -> dict:
    """
    Verify a Google Play subscription purchase token and update the user's tier.

    Args:
        user_id: Internal user ID
        product_id: Google Play product/SKU ID (e.g. "maigie_plus_monthly")
        purchase_token: The purchase token from the client

    Returns:
        Dict with verification result and updated tier info

    Raises:
        ValueError: If the purchase is invalid or cannot be verified
    """
    settings = get_settings()
    package_name = settings.GOOGLE_PLAY_PACKAGE_NAME

    try:
        service = _get_android_publisher_service()
        result = (
            service.purchases()
            .subscriptions()
            .get(
                packageName=package_name,
                subscriptionId=product_id,
                token=purchase_token,
            )
            .execute()
        )
    except Exception as e:
        logger.error(f"Google Play verification failed for user {user_id}: {e}", exc_info=True)
        raise ValueError(f"Failed to verify purchase with Google Play: {e}")

    # Check payment state
    # 0 = pending, 1 = received, 2 = free trial, 3 = deferred
    payment_state = result.get("paymentState")
    if payment_state is None and result.get("cancelReason") is not None:
        raise ValueError("Subscription has been cancelled")

    # Check expiry
    expiry_time_millis = int(result.get("expiryTimeMillis", 0))
    expiry_dt = datetime.fromtimestamp(expiry_time_millis / 1000, tz=timezone.utc)
    now = datetime.now(timezone.utc)

    if expiry_dt < now:
        raise ValueError("Subscription has expired")

    # Map to tier
    new_tier = _sku_to_tier(product_id)
    if new_tier == "FREE":
        raise ValueError(f"Unknown product ID: {product_id}")

    # Acknowledge the subscription if not already acknowledged
    if not result.get("acknowledgementState"):
        try:
            service.purchases().subscriptions().acknowledge(
                packageName=package_name,
                subscriptionId=product_id,
                token=purchase_token,
                body={},
            ).execute()
            logger.info(f"Acknowledged subscription {product_id} for user {user_id}")
        except Exception as e:
            # Non-fatal — the subscription is still valid
            logger.warning(f"Failed to acknowledge subscription: {e}")

    # Update user in database
    start_time_millis = int(result.get("startTimeMillis", 0))
    start_dt = datetime.fromtimestamp(start_time_millis / 1000, tz=timezone.utc)

    await db.user.update(
        where={"id": user_id},
        data={
            "tier": new_tier,
            "googlePlayPurchaseToken": purchase_token,
            "googlePlayProductId": product_id,
            "subscriptionCurrentPeriodStart": start_dt,
            "subscriptionCurrentPeriodEnd": expiry_dt,
        },
    )

    logger.info(
        f"User {user_id} verified Google Play subscription: "
        f"product={product_id}, tier={new_tier}, expires={expiry_dt.isoformat()}"
    )

    return {
        "verified": True,
        "tier": new_tier,
        "expiresAt": expiry_dt.isoformat(),
        "startedAt": start_dt.isoformat(),
        "paymentState": payment_state,
        "autoRenewing": result.get("autoRenewing", False),
    }


async def handle_rtdn_notification(message_data: dict) -> None:
    """
    Process a Real-Time Developer Notification from Google Play.

    Called by the webhook endpoint when Google sends a Pub/Sub message
    about a subscription state change.

    Args:
        message_data: Decoded notification payload
    """
    subscription_notification = message_data.get("subscriptionNotification")
    if not subscription_notification:
        logger.debug("RTDN message is not a subscription notification, skipping")
        return

    notification_type = subscription_notification.get("notificationType")
    purchase_token = subscription_notification.get("purchaseToken")
    subscription_id = subscription_notification.get("subscriptionId")

    logger.info(
        f"RTDN: type={notification_type}, subscription={subscription_id}"
    )

    if not purchase_token:
        logger.warning("RTDN missing purchaseToken, cannot process")
        return

    # Find the user with this purchase token
    user = await db.user.find_first(
        where={"googlePlayPurchaseToken": purchase_token}
    )

    if not user:
        logger.warning(
            f"RTDN: No user found for purchase token (subscription={subscription_id}). "
            "This may be a new purchase not yet verified."
        )
        return

    # Notification types:
    # 1 = RECOVERED, 2 = RENEWED, 3 = CANCELED, 4 = PURCHASED,
    # 5 = ON_HOLD, 6 = IN_GRACE_PERIOD, 7 = RESTARTED,
    # 9 = DEFERRED, 12 = REVOKED, 13 = EXPIRED
    CANCEL_TYPES = {3, 5, 12, 13}  # canceled, on_hold, revoked, expired
    ACTIVE_TYPES = {1, 2, 4, 7}  # recovered, renewed, purchased, restarted

    if notification_type in CANCEL_TYPES:
        # Downgrade user to FREE
        await db.user.update(
            where={"id": user.id},
            data={"tier": "FREE"},
        )
        logger.info(f"RTDN: Downgraded user {user.id} to FREE (type={notification_type})")

    elif notification_type in ACTIVE_TYPES:
        # Re-verify the subscription to get fresh expiry
        try:
            await verify_subscription(user.id, subscription_id, purchase_token)
            logger.info(f"RTDN: Re-verified user {user.id} subscription (type={notification_type})")
        except ValueError as e:
            logger.warning(f"RTDN: Re-verification failed for user {user.id}: {e}")
            # If verification fails, don't change tier — might be a transient issue

    else:
        logger.debug(f"RTDN: Unhandled notification type {notification_type} for user {user.id}")
