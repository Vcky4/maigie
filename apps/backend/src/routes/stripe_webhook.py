"""
Stripe webhook handler for subscription events.

This module handles Stripe webhook events via webhook destinations to keep subscription data in sync.

Copyright (C) 2025 Maigie
"""

import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response

from ..config import Settings, get_settings
from ..core.database import db
from ..services.subscription_service import handle_subscription_webhook

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
    settings: Settings = Depends(get_settings),
):
    """
    Handle Stripe webhook events from webhook destinations.

    This endpoint processes various Stripe events related to subscriptions:
    - customer.subscription.created
    - customer.subscription.updated
    - customer.subscription.deleted
    - invoice.payment_succeeded
    - invoice.payment_failed

    Configure webhook destinations in Stripe Dashboard:
    1. Go to Developers → Webhook destinations
    2. Create a destination pointing to: https://your-api-domain.com/api/v1/webhooks/stripe
    3. Set STRIPE_WEBHOOK_DESTINATION_ID in environment variables
    4. Copy the signing secret and set STRIPE_WEBHOOK_SECRET

    Args:
        request: FastAPI request object
        stripe_signature: Stripe signature header for verification
        settings: Application settings

    Returns:
        200 OK response
    """
    # Read raw body (must be bytes for signature verification)
    body = await request.body()

    # Check for destination ID in headers (if using webhook destinations)
    destination_id = request.headers.get("stripe-destination-id")
    if destination_id and settings.STRIPE_WEBHOOK_DESTINATION_ID:
        if destination_id != settings.STRIPE_WEBHOOK_DESTINATION_ID:
            logger.warning(
                f"Webhook destination ID mismatch: received {destination_id}, "
                f"expected {settings.STRIPE_WEBHOOK_DESTINATION_ID}"
            )
        else:
            logger.debug(f"Webhook event from destination: {destination_id}")

    # Verify signature and parse event
    try:
        if not settings.STRIPE_WEBHOOK_SECRET:
            logger.warning("STRIPE_WEBHOOK_SECRET not configured, skipping signature verification")
            import json

            event = json.loads(body)
        else:
            event = stripe.Webhook.construct_event(
                body, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
            )
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        )
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Error processing webhook",
        )

    event_type = event.get("type")
    event_data = event.get("data", {})

    logger.info(f"Received Stripe webhook event: {event_type}")

    # Handle subscription-related events
    if event_type.startswith("customer.subscription."):
        subscription = event_data.get("object", {})
        try:
            await handle_subscription_webhook(
                event_type=event_type,
                subscription=subscription,
                db_client=db,
            )
        except Exception as e:
            logger.error(f"Error handling subscription webhook: {e}")
            # Return 200 to prevent Stripe from retrying
            # Log the error for manual investigation
            return Response(status_code=200)

    # Handle invoice events
    elif event_type == "invoice.payment_succeeded":
        invoice = event_data.get("object", {})
        subscription_id = invoice.get("subscription")
        if subscription_id:
            try:
                # Retrieve full subscription object from Stripe
                subscription_obj = stripe.Subscription.retrieve(subscription_id)
                await handle_subscription_webhook(
                    event_type="customer.subscription.updated",
                    subscription=subscription_obj.to_dict(),
                    db_client=db,
                )
            except Exception as e:
                logger.error(f"Error handling invoice payment succeeded: {e}")

    elif event_type == "invoice.payment_failed":
        invoice = event_data.get("object", {})
        subscription_id = invoice.get("subscription")
        if subscription_id:
            try:
                # Retrieve full subscription object from Stripe
                subscription_obj = stripe.Subscription.retrieve(subscription_id)
                await handle_subscription_webhook(
                    event_type="customer.subscription.updated",
                    subscription=subscription_obj.to_dict(),
                    db_client=db,
                )
            except Exception as e:
                logger.error(f"Error handling invoice payment failed: {e}")

    # Return 200 to acknowledge receipt
    return Response(status_code=200)
