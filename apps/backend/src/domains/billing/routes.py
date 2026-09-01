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
from .services import credit_service, subscription_service

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
# Subscriptions (Paystack) — written, not mounted
# ===========================================================================
#
# `/subscriptions/paystack/initialize` and `/subscriptions/paystack/verify` are the single
# largest gap between this phase and a working money path, and they are absent because they
# cannot work yet.
#
# `paystack_service` holds a `PrismaClientRemoved` sentinel where its database used to be.
# `initialize_paystack_subscription`, `verify_paystack_transaction`,
# `cancel_paystack_subscription` and `handle_paystack_webhook` all reach it, so all four
# fail. The webhook fails quietly — `webhooks.py` catches and answers 200 — but these two
# routes would answer 500.
#
# That matters more than it first looks. Paystack is the NGN rail and Nigeria is the launch
# market; the naira prices are set independently rather than converted precisely because FX
# parity would price Maigie above Netflix Standard there. Mounting Stripe without Paystack
# makes the money path reachable in the markets we are not launching in and unreachable in
# the one we are. Porting `paystack_service` to SQLAlchemy is a launch blocker, not a later
# phase.
#
# Absent rather than mounted-and-broken: a 404 tells a client the path does not exist yet,
# which is true. A 500 tells it we are broken, and invites a retry.


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
