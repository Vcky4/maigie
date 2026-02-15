"""
Paystack webhook handler for subscription events.

Copyright (C) 2025 Maigie
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import Response

from ..config import get_settings
from ..core.database import db
from ..services.paystack_subscription_service import handle_paystack_webhook

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _verify_paystack_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Paystack webhook signature using HMAC SHA512."""
    computed = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


@router.post("/paystack")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(..., alias="x-paystack-signature"),
):
    """
    Handle Paystack webhook events for subscriptions.

    Paystack signs the payload with your Secret Key (not a separate webhook secret).
    Configure in Paystack Dashboard:
    1. Go to Settings → Webhooks
    2. Add URL: https://your-api.com/api/v1/webhooks/paystack
    3. Select events: subscription.create, subscription.disable, charge.success
    """
    body = await request.body()
    settings = get_settings()

    if settings.PAYSTACK_SECRET_KEY and not _verify_paystack_signature(
        body, x_paystack_signature, settings.PAYSTACK_SECRET_KEY
    ):
        logger.error("Invalid Paystack webhook signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )

    try:
        import json

        event = json.loads(body)
    except Exception as e:
        logger.error(f"Invalid Paystack webhook payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        )

    event_type = event.get("event")
    logger.info(f"Received Paystack webhook: {event_type}")

    try:
        await handle_paystack_webhook(
            event=event_type or "",
            payload=event,
            db_client=db,
        )
    except Exception as e:
        logger.error(f"Error handling Paystack webhook: {e}")
        return Response(status_code=200)

    return Response(status_code=200)
