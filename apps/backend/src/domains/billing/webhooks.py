"""
Billing domain — Webhook handlers for payment providers.

Stripe, Paystack, and Google Play RTDN (Real-Time Developer Notifications)
all funnel through here.
"""

import hashlib
import hmac
import json
import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response

from src.config import Settings, get_settings
from src.shared.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


# ===========================================================================
# Stripe Webhook
# ===========================================================================


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
    settings: Settings = Depends(get_settings),
):
    """Handle Stripe webhook events (subscriptions + one-time payments)."""
    body = await request.body()

    # Verify signature
    try:
        if not settings.STRIPE_WEBHOOK_SECRET:
            logger.warning("STRIPE_WEBHOOK_SECRET not configured, skipping verification")
            event = json.loads(body)
        else:
            event = stripe.Webhook.construct_event(
                body, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
            )
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Signature verification failed: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    # Delegate to subscription service
    try:
        from src.services.subscription_service import handle_subscription_webhook

        await handle_subscription_webhook(event, db)
    except Exception as e:
        logger.error(f"Error handling Stripe webhook: {e}", exc_info=True)
        # Return 200 to prevent Stripe from retrying (we log the error)

    return Response(status_code=200)


# ===========================================================================
# Paystack Webhook
# ===========================================================================


def _verify_paystack_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Paystack webhook signature using HMAC SHA512."""
    computed = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


@router.post("/paystack")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(..., alias="x-paystack-signature"),
):
    """Handle Paystack webhook events for subscriptions."""
    body = await request.body()
    settings = get_settings()

    # Verify signature
    if settings.PAYSTACK_SECRET_KEY and not _verify_paystack_signature(
        body, x_paystack_signature, settings.PAYSTACK_SECRET_KEY
    ):
        logger.error("Invalid Paystack webhook signature")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    try:
        event = json.loads(body)
        from src.services.paystack_subscription_service import handle_paystack_webhook

        await handle_paystack_webhook(event, db)
    except Exception as e:
        logger.error(f"Error handling Paystack webhook: {e}", exc_info=True)

    return Response(status_code=200)


# ===========================================================================
# Google Play RTDN (Real-Time Developer Notifications)
# ===========================================================================


@router.post("/google-play/rtdn")
async def google_play_rtdn(request: Request):
    """Handle Google Play Real-Time Developer Notifications via Pub/Sub."""
    try:
        body = await request.json()
        from src.services.google_play_billing_service import handle_rtdn_notification

        await handle_rtdn_notification(body)
    except Exception as e:
        logger.error(f"Error handling Google Play RTDN: {e}", exc_info=True)

    return Response(status_code=200)
