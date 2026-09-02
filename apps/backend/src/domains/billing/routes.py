"""
Billing domain — API routes.

Covers the plan catalog, subscriptions (Stripe, Paystack, Google Play), purchase
history, and admin credit adjustments. Webhooks are in a separate module
(webhooks.py) mounted at /api/v1/webhooks.

Mounted at: /api/v1/billing
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.config import Settings, get_settings
from src.shared.auth import CurrentUser, StaffUser

from . import models
from .services import credit_service, entitlement_service, subscription_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])


# ===========================================================================
# Plans (Public)
# ===========================================================================


@router.get("/plans/catalog", response_model=models.PlanCatalogResponse)
async def plan_catalog():
    """Return the active product catalog (no auth required)."""
    return await subscription_service.get_plan_catalog()


# ===========================================================================
# Entitlement
# ===========================================================================


@router.get("/entitlement", response_model=models.EntitlementResponse)
async def entitlement(current_user: CurrentUser):
    """Resolve the caller's personal entitlement.

    One endpoint over the one resolver, so a client never has to reconstruct entitlement from
    `User.tier` plus a trial field plus, later, a pass. Every gate on the server already reads
    `entitlement_service.resolve`; this is the same answer, so a locked panel and the screen that
    explains why it is locked cannot disagree.

    Personal scope only. A learner's seat in a Space is a different question with a different answer
    and is not served here (Decision F).
    """
    resolved = await entitlement_service.resolve(current_user.id)
    return models.EntitlementResponse(
        tier=resolved.tier,
        source=resolved.source,
        expiresAt=resolved.expires_at,
        passId=resolved.pass_id,
        subscriptionTier=resolved.subscription_tier,
        isTrial=resolved.is_trial,
        trialDaysRemaining=resolved.trial_days_remaining,
        windowAllowance=resolved.window_allowance,
    )


# ===========================================================================
# Subscriptions (Stripe)
# ===========================================================================


@router.post("/subscriptions/checkout", response_model=models.CheckoutResponse)
async def create_checkout(
    body: models.CheckoutRequest,
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """Create a Stripe checkout session for a subscription plan."""
    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    success_url = f"{base_url}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/subscription/cancel"

    try:
        result = await subscription_service.create_checkout_session(
            user=current_user,
            plan_id=body.plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return models.CheckoutResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/subscriptions/sync-checkout")
async def sync_checkout(body: models.SyncCheckoutRequest, current_user: CurrentUser):
    """Sync subscription from completed Stripe checkout session."""
    updated = await subscription_service.sync_from_checkout(
        session_id=body.session_id, user_id=current_user.id
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Could not sync subscription")
    return {
        "tier": updated.tier,
        "stripe_subscription_id": updated.stripe_subscription_id,
    }


@router.post("/subscriptions/portal", response_model=models.PortalResponse)
async def create_portal(
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """Create a Stripe customer portal session."""
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer account")

    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    result = await subscription_service.create_portal_session(
        user=current_user, return_url=f"{base_url}/subscription"
    )
    return models.PortalResponse(**result)


@router.post("/subscriptions/cancel", response_model=models.CancelSubscriptionResponse)
async def cancel_subscription(current_user: CurrentUser):
    """Cancel the active subscription (Stripe or Paystack)."""
    # current_user is the SQLAlchemy User model: attributes are snake_case even
    # though the underlying columns are camelCase.
    provider = current_user.payment_provider
    if not provider:
        if current_user.paystack_subscription_code:
            provider = "paystack"
        elif current_user.stripe_subscription_id:
            provider = "stripe"

    if not provider:
        raise HTTPException(status_code=400, detail="No active subscription")

    result = await subscription_service.cancel_subscription(user=current_user)
    return models.CancelSubscriptionResponse(
        status=result["status"],
        cancel_at_period_end=result["cancel_at_period_end"],
        current_period_end=(
            result["current_period_end"].isoformat()
            if hasattr(result["current_period_end"], "isoformat")
            else str(result["current_period_end"])
        ),
    )


# ===========================================================================
# Subscriptions (Paystack) — the NGN rail
# ===========================================================================
#
# Mounted in Phase 2b, once `paystack_service` was ported off Prisma. These two routes were the
# single largest gap between a mounted router and a working money path: Paystack is the NGN rail and
# Nigeria is the launch market, so Stripe alone made the money path reachable in the markets we are
# not launching in and unreachable in the one we are.


@router.post(
    "/subscriptions/paystack/initialize",
    response_model=models.PaystackInitializeResponse,
)
async def paystack_initialize(
    body: models.PaystackInitializeRequest,
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """Initialize a Paystack subscription (NGN)."""
    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    success_url = body.success_url or f"{base_url}/subscription/paystack/success"
    cancel_url = body.cancel_url or f"{base_url}/subscription/cancel"

    try:
        result = await subscription_service.initialize_paystack(
            user=current_user,
            plan_id=body.plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return models.PaystackInitializeResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/subscriptions/paystack/verify", response_model=models.PaystackVerifyResponse)
async def paystack_verify(reference: str, current_user: CurrentUser):
    """Verify a Paystack transaction after the learner returns from the payment page."""
    updated = await subscription_service.verify_paystack(
        reference=reference, user_id=current_user.id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not verify transaction",
        )
    return models.PaystackVerifyResponse(
        tier=str(updated.tier),
        paystack_subscription_code=updated.paystack_subscription_code,
    )


# ===========================================================================
# Subscriptions (Google Play)
# ===========================================================================


@router.post("/subscriptions/google-play/verify", response_model=models.GooglePlayVerifyResponse)
async def google_play_verify(body: models.GooglePlayVerifyRequest, current_user: CurrentUser):
    """Verify a Google Play subscription purchase."""
    try:
        result = await subscription_service.verify_google_play_subscription(
            user_id=current_user.id,
            product_id=body.productId,
            purchase_token=body.purchaseToken,
            base_plan_id=body.basePlanId,
        )
        return models.GooglePlayVerifyResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# The in-app product verification endpoint that used to live here verified a credit-pack
# purchase and granted credits. Credit packs are withdrawn, so it verified a product that
# no longer exists. Its replacement verifies a Plus pass purchase and grants an inactive
# pass; it arrives with the purchase rails, alongside the Apple equivalent.
# `google_play_service.verify_product_purchase` is left in place as the basis for it —
# the `purchases.products.get` call and the token-replay check are both reusable.


# ===========================================================================
# Credits
# ===========================================================================
#
# The credit-pack catalog and purchase endpoints are gone with the product. What is left
# is history and a support tool: both describe transactions that really happened.


@router.get("/credits/purchases", response_model=models.PaginatedPurchaseHistory)
async def get_purchase_history(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """Get paginated credit purchase history."""
    return await credit_service.get_purchase_history(
        user_id=current_user.id, page=page, page_size=pageSize
    )


@router.post("/admin/credits/adjust", response_model=models.AdminCreditAdjustResponse)
async def admin_adjust_credits(body: models.AdminCreditAdjustRequest, admin_user: StaffUser):
    """Admin: adjust a user's credit balance."""
    try:
        updated = await credit_service.admin_adjust_balance(
            admin_id=admin_user.id,
            target_user_id=body.userId,
            amount=body.amount,
            reason=body.reason,
        )
        return models.AdminCreditAdjustResponse(
            userId=updated.id,
            newBalance=updated.purchased_credits_balance or 0,
            adjustmentAmount=body.amount,
        )
    except Exception as e:
        logger.error(f"Credit adjustment failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to adjust credits")


# ===========================================================================
# Referrals — not mounted
# ===========================================================================
#
# `/referrals/stats`, `/referrals/claimable` and `/referrals/claim` are deleted here, and
# not because they were unwritten. All three resolved into `referral_rewards_service`,
# which was written against the Prisma client and now holds a `PrismaClientRemoved`
# sentinel where its database used to be. Every one of them would answer 500. Mounting the
# router with them attached would take three endpoints that are currently *honestly*
# unreachable and make them dishonestly reachable.
#
# They return when the reward they describe exists. It is no longer a token grant: it is
# points, earned when a referred learner has genuinely studied on seven distinct days,
# redeemable for passes and for nothing else. That is a different contract, not a port,
# which is why these are removed rather than commented out.


# ===========================================================================
# Ads (Rewarded Video) — withdrawn
# ===========================================================================
#
# `/ads/stats` and `/ads/reward` are gone. See `services/credit_service.py` for the
# reasoning: the reward was an invisible daily credit-limit increase, and nothing in the
# product asks a learner to watch an advertisement.
