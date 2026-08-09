"""
Billing domain — API routes.

Covers subscriptions, credits, plans, referrals, ads, and Google Play billing.
Webhooks are in a separate module (webhooks.py) mounted at /api/v1/webhooks.

Mounted at: /api/v1/billing
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.config import Settings, get_settings
from src.domains.identity.db_models import User
from src.shared.auth import CurrentUser, StaffUser
from src.shared.exceptions import NotFoundError, ValidationError

from . import models
from .services import credit_service, referral_service, subscription_service

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
    return {"tier": updated.tier, "stripe_subscription_id": updated.stripeSubscriptionId}


@router.post("/subscriptions/portal", response_model=models.PortalResponse)
async def create_portal(
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """Create a Stripe customer portal session."""
    if not current_user.stripeCustomerId:
        raise HTTPException(status_code=400, detail="No Stripe customer account")

    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    result = await subscription_service.create_portal_session(
        user=current_user, return_url=f"{base_url}/subscription"
    )
    return models.PortalResponse(**result)


@router.post("/subscriptions/cancel", response_model=models.CancelSubscriptionResponse)
async def cancel_subscription(current_user: CurrentUser):
    """Cancel the active subscription (Stripe or Paystack)."""
    provider = current_user.paymentProvider
    if not provider:
        if current_user.paystackSubscriptionCode:
            provider = "paystack"
        elif current_user.stripeSubscriptionId:
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
# Subscriptions (Paystack)
# ===========================================================================


@router.post("/subscriptions/paystack/initialize", response_model=models.PaystackInitializeResponse)
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
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subscriptions/paystack/verify", response_model=models.PaystackVerifyResponse)
async def paystack_verify(reference: str, current_user: CurrentUser):
    """Verify Paystack transaction after redirect."""
    updated = await subscription_service.verify_paystack(
        reference=reference, user_id=current_user.id
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Could not verify transaction")
    return models.PaystackVerifyResponse(
        tier=str(updated.tier),
        paystack_subscription_code=updated.paystackSubscriptionCode,
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


@router.post(
    "/subscriptions/google-play/verify-product",
    response_model=models.GooglePlayProductVerifyResponse,
)
async def google_play_verify_product(
    body: models.GooglePlayProductVerifyRequest, current_user: CurrentUser
):
    """Verify a Google Play in-app product (credit pack) purchase."""
    try:
        result = await subscription_service.verify_google_play_product(
            user_id=current_user.id,
            product_id=body.productId,
            purchase_token=body.purchaseToken,
        )
        return models.GooglePlayProductVerifyResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===========================================================================
# Credits
# ===========================================================================


@router.get("/credit-packs", response_model=list[models.CreditPackResponse])
async def get_credit_packs(current_user: CurrentUser):
    """List available credit packs with user-specific pricing."""
    return await credit_service.get_credit_packs(current_user)


@router.post("/credit-packs/purchase", response_model=models.PurchaseSessionResponse)
async def purchase_credit_pack(body: models.PurchaseInitiateRequest, current_user: CurrentUser):
    """Initiate a credit pack purchase."""
    from src.shared.infrastructure.rate_limit import enforce_rate_limit
    from src.shared.infrastructure.redis import cache

    await enforce_rate_limit(
        user_id=current_user.id,
        endpoint="credit_pack_purchase",
        max_requests=5,
        window_seconds=60,
    )
    try:
        return await credit_service.initiate_purchase(
            user=current_user,
            pack_id=body.packId,
            success_url=body.successUrl,
            cancel_url=body.cancelUrl,
        )
    except Exception as e:
        logger.error(f"Purchase initiation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to initiate purchase")


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
            newBalance=updated.purchasedCreditsBalance or 0,
            adjustmentAmount=body.amount,
        )
    except Exception as e:
        logger.error(f"Credit adjustment failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to adjust credits")


# ===========================================================================
# Referrals
# ===========================================================================


@router.get("/referrals/stats", response_model=models.ReferralStatsResponse)
async def get_referral_stats(current_user: CurrentUser):
    """Get referral statistics."""
    stats = await referral_service.get_referral_stats(current_user)
    return models.ReferralStatsResponse(**stats)


@router.get("/referrals/claimable", response_model=list[models.ClaimableRewardResponse])
async def get_claimable_rewards(current_user: CurrentUser):
    """Get all claimable referral rewards."""
    rewards = await referral_service.get_claimable_rewards(current_user)
    return [models.ClaimableRewardResponse(**r) for r in rewards]


@router.post("/referrals/claim", response_model=models.ClaimRewardResponse)
async def claim_referral_reward(body: models.ClaimRewardRequest, current_user: CurrentUser):
    """Claim a referral reward."""
    try:
        result = await referral_service.claim_reward(current_user, body.rewardId)
        return models.ClaimRewardResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===========================================================================
# Ads (Rewarded Video)
# ===========================================================================


@router.get("/ads/stats", response_model=models.AdStatsResponse)
async def get_ad_stats(current_user: CurrentUser):
    """Get ad watch statistics."""
    stats = await credit_service.get_ad_stats(current_user.id)
    return models.AdStatsResponse(**stats)


@router.post("/ads/reward", response_model=models.AdRewardResponse)
async def claim_ad_reward(body: models.AdRewardRequest, current_user: CurrentUser):
    """Claim credits for watching a rewarded ad."""
    result = await credit_service.claim_ad_reward(
        user_id=current_user.id,
        ad_type=body.adType,
        ad_unit_id=body.adUnitId,
    )
    return models.AdRewardResponse(**result)
