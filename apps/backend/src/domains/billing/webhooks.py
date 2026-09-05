"""
Billing domain — Webhook handlers for payment providers.

Stripe, Paystack, and Google Play RTDN (Real-Time Developer Notifications)
all funnel through here.

**Every handler fails closed.** These three endpoints are unauthenticated by construction — a
payment provider cannot present a bearer token for one of our users — so the signature *is* the
authentication, and an unverifiable request is refused rather than trusted. That was not true until
Phase 2a: all three had been written to fail open while the router was commented out in `app.py`,
so mounting the router in Phase 1 turned three trusting endpoints live at once without a line of
this file appearing in the diff. The Stripe handler writes `User.tier`, so the open case let any
caller grant themselves `PREMIUM_MONTHLY` on a deployment whose secret was unset.

Two rules, both inherited from `notifications.email_webhooks`, which had already reasoned this
through for Resend:

**A missing secret is a refusal, not a bypass.** An unconfigured deployment answers `503` and
processes nothing. The opposite default treats "we forgot to configure this" as "trust everyone",
and the two are indistinguishable from the outside right up to the point where they are not.

**Only a successfully processed event answers `200`.** A handler failure answers `500` so the
provider retries, because these events carry money: a swallowed `charge.success` is a payment with
no record, and a swallowed `subscription.disable` leaves a cancelled subscriber entitled. The
previous code logged and returned `200` for every exception, which is indistinguishable to the
provider from having done the work.
"""

import hashlib
import hmac
import json
import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _unconfigured(provider: str) -> HTTPException:
    """The refusal for a deployment that cannot verify this provider's signature.

    `503` rather than `500`: the request is well-formed and the caller is probably legitimate, we
    simply cannot prove it. Providers retry on `503`, so a real event survives the window between
    mounting the endpoint and setting the secret instead of being lost.
    """
    logger.error(
        "%s webhook secret is not configured — refusing ingestion. "
        "Set it, or unmount the webhook router.",
        provider,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{provider} webhook verification is not configured",
    )


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

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise _unconfigured("Stripe")

    try:
        event = stripe.Webhook.construct_event(
            body, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid Stripe payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Stripe signature verification failed: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    from src.domains.billing.services.stripe_service import handle_stripe_event

    try:
        await handle_stripe_event(event)
    except Exception as e:
        # `500`, not `200`. This handler writes `User.tier` and the subscription period; losing an
        # event silently means a subscriber whose state never changed. Stripe retries with backoff.
        logger.error(f"Error handling Stripe webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        ) from e

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
    settings: Settings = Depends(get_settings),
):
    """Handle Paystack webhook events for subscriptions."""
    body = await request.body()

    # Previously `if settings.PAYSTACK_SECRET_KEY and not _verify(...)`, so an empty key skipped
    # verification rather than failing it — the whole condition short-circuited to False and the
    # event was processed unverified. This is the launch market's rail.
    if not settings.PAYSTACK_SECRET_KEY:
        raise _unconfigured("Paystack")

    if not _verify_paystack_signature(body, x_paystack_signature, settings.PAYSTACK_SECRET_KEY):
        logger.error("Invalid Paystack webhook signature")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    try:
        event = json.loads(body)
    except ValueError as e:
        logger.error(f"Invalid Paystack payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    from src.domains.billing.services.paystack_service import handle_paystack_webhook

    try:
        await handle_paystack_webhook(event, None)
    except Exception as e:
        # Until Phase 2b ports this service it reaches a `PrismaClientRemoved` sentinel and raises,
        # which is exactly why this must not answer `200`: a real NGN charge would otherwise produce
        # no record, no tier change and no alert.
        logger.error(f"Error handling Paystack webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        ) from e

    return Response(status_code=200)


# ===========================================================================
# Google Play RTDN (Real-Time Developer Notifications)
# ===========================================================================


def _verify_pubsub_oidc(authorization: str | None, *, settings: Settings) -> None:
    """Authenticate a Pub/Sub push request by its OIDC token.

    Google Play RTDN arrives as a Pub/Sub push, and Pub/Sub authenticates pushes by signing a
    short-lived OIDC JWT with the push subscription's service account and sending it as
    ``Authorization: Bearer``. Verifying it against Google's public certs, with the audience we
    configured on the subscription, is the only thing that distinguishes a real notification from a
    `curl`.

    **This endpoint previously had no authentication of any kind, under any configuration** — it
    read the body and dispatched. It was unreachable because the router was unmounted, which is the
    only reason that was not a live hole.

    Raises `503` when unconfigured and `401` when the token is absent or invalid.
    """
    if not settings.GOOGLE_PUBSUB_AUDIENCE:
        raise _unconfigured("Google Play RTDN")

    if not authorization or not authorization.lower().startswith("bearer "):
        logger.error("Google Play RTDN request carried no bearer token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_PUBSUB_AUDIENCE
        )
    except Exception as e:
        logger.error(f"Google Play RTDN token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
        ) from e

    expected = settings.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL
    if expected and claims.get("email") != expected:
        # A valid Google-signed token proves Google minted it, not that *our* subscription sent it.
        # Without this check any Google customer could push to this endpoint.
        logger.error(
            "Google Play RTDN token was for %s, expected %s",
            claims.get("email"),
            expected,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unexpected token issuer"
        )


@router.post("/google-play/rtdn")
async def google_play_rtdn(
    request: Request,
    authorization: str | None = Header(None),
    settings: Settings = Depends(get_settings),
):
    """Handle Google Play Real-Time Developer Notifications via Pub/Sub."""
    _verify_pubsub_oidc(authorization, settings=settings)

    try:
        body = await request.json()
    except ValueError as e:
        logger.error(f"Invalid Google Play RTDN payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    from src.domains.billing.services.google_play_service import (
        handle_rtdn_notification,
    )

    try:
        await handle_rtdn_notification(body)
    except Exception as e:
        logger.error(f"Error handling Google Play RTDN: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        ) from e

    return Response(status_code=200)
