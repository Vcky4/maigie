"""
Google Play Billing routes for Android in-app subscription verification and RTDN webhooks.
"""

import base64
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..dependencies import CurrentUser
from ..services.google_play_billing_service import (
    handle_rtdn_notification,
    verify_subscription,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions/google-play", tags=["google-play-billing"])


class VerifyPurchaseRequest(BaseModel):
    """Request to verify a Google Play subscription purchase."""

    productId: str  # e.g. "maigie_plus_monthly"
    purchaseToken: str


class VerifyPurchaseResponse(BaseModel):
    """Response after verifying a purchase."""

    verified: bool
    tier: str
    expiresAt: str
    startedAt: str
    autoRenewing: bool


@router.post("/verify", response_model=VerifyPurchaseResponse)
async def verify_google_play_purchase(
    request: VerifyPurchaseRequest,
    current_user: CurrentUser,
):
    """
    Verify a Google Play subscription purchase token.

    Called by the mobile app after a successful purchase to validate
    the purchase with Google's servers and update the user's tier.
    """
    try:
        result = await verify_subscription(
            user_id=current_user.id,
            product_id=request.productId,
            purchase_token=request.purchaseToken,
        )
        return VerifyPurchaseResponse(
            verified=result["verified"],
            tier=result["tier"],
            expiresAt=result["expiresAt"],
            startedAt=result["startedAt"],
            autoRenewing=result["autoRenewing"],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error verifying purchase: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify purchase",
        )


@router.post("/webhook")
async def google_play_rtdn_webhook(request: Request):
    """
    Receive Real-Time Developer Notifications (RTDN) from Google Play via Pub/Sub.

    Google sends a POST with a Pub/Sub message containing a base64-encoded
    notification about subscription state changes.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Pub/Sub message format: { "message": { "data": "<base64>", ... }, "subscription": "..." }
    message = body.get("message", {})
    data_b64 = message.get("data")

    if not data_b64:
        logger.warning("RTDN webhook received without message data")
        # Return 200 to avoid Pub/Sub retries for malformed messages
        return {"status": "ignored"}

    try:
        decoded = base64.b64decode(data_b64)
        notification_data = json.loads(decoded)
    except Exception as e:
        logger.error(f"RTDN webhook: Failed to decode message data: {e}")
        return {"status": "decode_error"}

    try:
        await handle_rtdn_notification(notification_data)
    except Exception as e:
        logger.error(f"RTDN webhook: Error processing notification: {e}", exc_info=True)
        # Still return 200 to prevent infinite retries
        return {"status": "error"}

    return {"status": "ok"}
