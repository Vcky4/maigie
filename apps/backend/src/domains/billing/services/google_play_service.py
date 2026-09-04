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
from datetime import UTC, datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build

from src.config import get_settings
from src.domains.billing.repository import billing_repo
from src.domains.identity.repository import IdentityRepository
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def _get_android_publisher_service():
    """Build the Google Play Developer API client using service account credentials."""
    settings = get_settings()

    if settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
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
    # For subscriptions, the product_id is always the subscription ID (maigie_plus)
    # We determine the tier from the basePlanId passed separately
    if product_id == settings.GOOGLE_PLAY_SUBSCRIPTION_ID:
        # Default to monthly — the caller should use _base_plan_to_tier instead
        return "PREMIUM_MONTHLY"
    return "FREE"


def _base_plan_to_tier(base_plan_id: str) -> str:
    """Map a Google Play base plan ID to the internal tier enum."""
    settings = get_settings()
    if base_plan_id == settings.GOOGLE_PLAY_BASE_PLAN_MONTHLY:
        return "PREMIUM_MONTHLY"
    # The yearly base plan is withdrawn (Non-goals): its `plus-yearly` id is deleted in the console
    # and `PREMIUM_YEARLY` is carried in no tier set, so an unrecognised base plan is `FREE` rather
    # than a tier nothing can grant.
    return "FREE"


async def verify_subscription(
    user_id: str,
    product_id: str,
    purchase_token: str,
    base_plan_id: str = "",
) -> dict:
    """
    Verify a Google Play subscription purchase token and update the user's tier.

    Args:
        user_id: Internal user ID
        product_id: Google Play subscription ID (e.g. "maigie_plus")
        purchase_token: The purchase token from the client
        base_plan_id: The base plan ID (e.g. "plus-monthly")

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
    expiry_dt = datetime.fromtimestamp(expiry_time_millis / 1000, tz=UTC)
    now = datetime.now(UTC)

    if expiry_dt < now:
        raise ValueError("Subscription has expired")

    # Determine tier from the base plan ID. There is one subscription base plan now — `plus-monthly`
    # (yearly is withdrawn) — so an absent base plan defaults to it rather than inferring a period
    # from the purchase duration, which only ever mattered to distinguish the retired yearly plan.
    new_tier = _base_plan_to_tier(base_plan_id) if base_plan_id else "PREMIUM_MONTHLY"

    if new_tier == "FREE":
        raise ValueError(f"Unknown base plan: {base_plan_id}")

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
    start_dt = datetime.fromtimestamp(start_time_millis / 1000, tz=UTC)

    identity_repo = IdentityRepository()
    await identity_repo.update(
        user_id,
        {
            "tier": new_tier,
            "googlePlayPurchaseToken": purchase_token,
            "googlePlayProductId": f"{product_id}:{base_plan_id}",
            "subscriptionCurrentPeriodStart": start_dt,
            "subscriptionCurrentPeriodEnd": expiry_dt,
            "paymentProvider": "google_play",
        },
    )

    logger.info(
        f"User {user_id} verified Google Play subscription: "
        f"product={product_id}, basePlan={base_plan_id}, tier={new_tier}, expires={expiry_dt.isoformat()}"
    )

    return {
        "verified": True,
        "tier": new_tier,
        "expiresAt": expiry_dt.isoformat(),
        "startedAt": start_dt.isoformat(),
        "paymentState": payment_state,
        "autoRenewing": result.get("autoRenewing", False),
    }


# `verify_product_purchase` and `_sku_to_credits` are removed.
#
# They verified a one-time Google Play purchase of `credit_pack_starter` / `_value` / `_power` and
# granted 50 000 / 165 000 / 575 000 credits into `User.purchasedCreditsBalance`. Phase 1 withdrew
# credit packs (§6.1) and Phase 3 dropped the column, so the function verified a product that cannot
# be listed and credited a column that does not exist.
#
# Phase 1 left it standing on the grounds that the `purchases.products.get` call and the
# token-replay check were reusable for passes. They are, but a function that compiles and cannot
# work is a worse reference than a note saying what to reuse, so here is the note. The pass rails
# need: `purchases().products().get()` for the state check, `purchaseState == 0`,
# `products().consume()` so the SKU can be bought again — a pass is genuinely consumable, which is
# exactly why Decision G makes the purchase record the source of truth rather than the store —
# and a replay guard keyed on `purchaseToken`. What they must *not* reuse is the credit grant: a
# pass purchase creates inactive `PlusPass` inventory, and the clock starts when the learner
# activates it (Decision A).


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

    logger.info(f"RTDN: type={notification_type}, subscription={subscription_id}")

    if not purchase_token:
        logger.warning("RTDN missing purchaseToken, cannot process")
        return

    # Find the user with this purchase token
    user = await billing_repo.find_user_by_google_play_token(purchase_token)

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
        identity_repo = IdentityRepository()
        await identity_repo.update(user.id, {"tier": "FREE"})
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
