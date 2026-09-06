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
from src.shared.exceptions import MaigieError

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


async def verify_product_purchase(user_id: str, product_id: str, purchase_token: str) -> dict:
    """Verify a one-time Google Play pass/voice purchase and grant it. Idempotent on the token.

    Replaces the credit-pack verifier this used to hold (withdrawn §6.1). Reuses what that function's
    removal note said was reusable — `purchases().products().get()` for the state, `purchaseState == 0`,
    and a replay guard — but grants a **pass** rather than credits: `purchase_service.fulfill_purchase`
    persists a `PlusPurchase` keyed on the `purchaseToken` and puts an inactive `PlusPass` in inventory,
    whose clock the learner starts on activation (Decision A). The unique `providerReference` is the
    replay/cross-account guard: a replayed token grants nothing, a token bound to another learner is
    `409 PURCHASE_ALREADY_CLAIMED`.

    **The voice pack requires an active Plus entitlement to buy** (Decision R). A learner with none is
    refused here and the purchase is not credited; left unacknowledged, Google auto-refunds it within a
    few days — refund-by-revocation, the one case a valid store receipt is deliberately not honoured.
    """
    from src.domains.billing.services import entitlement_service, purchase_service

    if product_id == purchase_service.VOICE_PACK_PRODUCT_ID:
        entitlement = await entitlement_service.resolve(user_id)
        if entitlement.tier != "plus":
            raise MaigieError(
                message=(
                    "The voice pack is an add-on to Maigie Plus. Start a subscription or activate a "
                    "pass first, then top up your voice minutes."
                ),
                status_code=403,
                code="VOICE_PACK_REQUIRES_PLUS",
            )

    settings = get_settings()
    package_name = settings.GOOGLE_PLAY_PACKAGE_NAME
    service = _get_android_publisher_service()

    result = (
        service.purchases()
        .products()
        .get(packageName=package_name, productId=product_id, token=purchase_token)
        .execute()
    )

    # purchaseState: 0 = purchased, 1 = canceled, 2 = pending. Only a completed purchase grants.
    purchase_state = result.get("purchaseState", 1)
    if purchase_state != 0:
        raise ValueError(
            f"Google Play purchase for {product_id} is not in the purchased state "
            f"(purchaseState={purchase_state})."
        )

    region = result.get("regionCode")
    amount_minor, currency = purchase_service.configured_store_amount(product_id, region)

    purchase = await purchase_service.fulfill_purchase(
        user_id=user_id,
        product_id=product_id,
        provider="google_play",
        provider_reference=purchase_token,
        amount_minor=amount_minor,
        currency=currency,
        raw_payload={"orderId": result.get("orderId"), "regionCode": region},
    )

    # Acknowledge so Google does not auto-refund a purchase we honoured. Non-fatal: the grant is done
    # and the purchase recorded, so a failed acknowledgement must not fail the caller — and the client's
    # own consumption (Play Billing) acknowledges too, so a double-ack here is expected and ignored.
    if not result.get("acknowledgementState"):
        try:
            service.purchases().products().acknowledge(
                packageName=package_name,
                productId=product_id,
                token=purchase_token,
                body={},
            ).execute()
        except Exception as e:
            logger.warning(f"Google Play product acknowledge failed (non-fatal): {e}")

    logger.info(
        "Google Play product verified: user=%s product=%s purchase=%s",
        user_id,
        product_id,
        purchase.id,
    )
    return {"verified": True, "productId": product_id, "purchaseId": purchase.id}


async def handle_rtdn_notification(message_data: dict) -> None:
    """
    Process a Real-Time Developer Notification from Google Play.

    Called by the webhook endpoint when Google sends a Pub/Sub message
    about a subscription state change.

    Args:
        message_data: Decoded notification payload
    """
    # A voided purchase — refund or chargeback — reaches every product type, and for a one-time pass or
    # voice pack it is the revocation signal (a subscription void also arrives as a `subscriptionNotification`
    # type below). Keyed on the `purchaseToken`, which is the `PlusPurchase.providerReference`: a pass
    # purchase is found and revoked, and a subscription void finds no purchase and is a harmless no-op.
    voided = message_data.get("voidedPurchaseNotification")
    if voided:
        token = voided.get("purchaseToken")
        if token:
            from src.domains.billing.services import purchase_service

            await purchase_service.refund_purchase(provider_reference=token)
        else:
            logger.warning("RTDN voided-purchase notification carried no purchaseToken")
        return

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
