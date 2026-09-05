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
from src.shared.exceptions import MaigieError

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
# Usage
# ===========================================================================


@router.get("/usage", response_model=models.UsageResponse)
async def usage(current_user: CurrentUser):
    """How much of the caller's current window is spent, and when it refills.

    Separate from `/entitlement` rather than folded into it, because the two change on different
    clocks: entitlement changes when a learner pays, activates a pass or a trial lapses, and usage
    changes on every operation. A client can poll this without re-resolving entitlement, and a cached
    entitlement does not go stale because usage moved.

    **This read does not reset anything.** A learner returning after six hours sees a full allowance
    because their window has elapsed, not because looking at it refilled it; the reset is attributed
    to the first billable operation afterwards. That is what makes `windowResetsAt` predictable — it
    is always five hours after a window some operation actually opened.

    Note that `GET /users/usage`, which the web client calls, has never existed server-side; it has
    been answering 404. This is the endpoint, and Phase 7 points the client at it.
    """
    from .services.credit_consumption_service import get_credit_usage

    raw = await get_credit_usage(current_user)
    return models.UsageResponse(
        tier=raw["tier"],
        windowResetsAt=raw["windowResetsAt"],
        percentUsed=raw["percentUsed"],
        isExhausted=raw["isExhausted"],
        monthlyPercentUsed=raw.get("monthlyPercentUsed"),
        monthlyExhausted=raw.get("monthlyExhausted"),
    )


@router.get("/voice/balance", response_model=models.VoiceBalanceResponse)
async def voice_balance(current_user: CurrentUser):
    """Live-voice minutes remaining, and whether voice is available at all.

    Ships with the counter it reads, because **a counter the learner cannot see is a counter they will
    be surprised by** — and being surprised mid-sentence by a tutor that stops talking is the worst
    version of that. `study_voice` refuses an unfunded session before opening a provider socket, so
    without this endpoint the first a learner would know is a refusal.

    Separate from `/usage` because voice is a separate meter (§6.3), and denominated in minutes rather
    than a percentage because minutes are what was sold.

    This is the one read in the billing domain that can *write*: `voice_service.read_balance` persists
    a re-grant when it finds the stored source stale, which is how a renewal tops the balance up
    without a sweep job. It is idempotent — a second call within the same period finds a source it
    recognises and writes nothing — so a polling client does not generate write load.
    """
    from .services import voice_service

    balance = await voice_service.read_balance(current_user.id)
    entitlement = await entitlement_service.resolve(current_user.id)
    return models.VoiceBalanceResponse(
        available=balance.available,
        minutesRemaining=balance.total_minutes,
        secondsRemaining=balance.total_seconds,
        minutesIncluded=entitlement.voice_seconds_included // 60,
        hasPurchasedMinutes=balance.purchased_seconds > 0,
    )


# ===========================================================================
# Passes
# ===========================================================================


def _pass_item(row) -> models.PlusPassItem:
    return models.PlusPassItem(
        id=row.id,
        productId=row.product_id,
        status=row.status,
        source=row.source,
        durationMinutes=row.duration_minutes,
        unitsAllowance=row.units_allowance,
        unitsUsed=row.units_used,
        activatedAt=row.activated_at,
        expiresAt=row.expires_at,
        endedReason=row.ended_reason,
        createdAt=row.created_at,
    )


@router.get("/passes", response_model=models.PlusPassListResponse)
async def list_passes(current_user: CurrentUser):
    """Every pass the learner holds — inventory, running, and ended.

    **The restore path for iOS, not just a list.** StoreKit does not return finished consumables from
    `Transaction.currentEntitlements`, so a reinstalled app cannot recover a purchased-but-unactivated
    pass from the device (Decision G). That is why the purchase is persisted at verification time and
    before anything is granted, and why "restore" for a pass means calling this rather than asking
    StoreKit.

    Ended passes are returned too: "what happened to my pass" is a question about one that ended.
    """
    from .services import pass_service

    rows = await pass_service.list_passes(current_user.id)
    return models.PlusPassListResponse(
        passes=[_pass_item(row) for row in rows],
        inventoryCount=sum(1 for row in rows if row.status == pass_service.STATUS_INVENTORY),
    )


@router.post("/passes/{pass_id}/activate", response_model=models.PlusPassItem)
async def activate_pass(pass_id: str, current_user: CurrentUser):
    """Start a pass's clock. **This is the moment the product begins**, not the purchase.

    Decision A: a pass is inventory until activated, so a $0.99 five-hour pass bought on Tuesday can be
    spent on Saturday's revision session. Decision D: `409 PASS_REDUNDANT` if the learner already has
    Plus from a subscription, a trial or another pass — and they keep the pass, since a refused
    activation consumes nothing.

    Activation also resets the usage window (Decision E). Without that, a five-hour pass activated at
    minute 290 of a Free window would deliver ten minutes and then a wall.

    The one-active invariant is a partial unique index rather than the check above it, so two concurrent
    activations produce one winner and one `409` instead of a race — `pass_service.activate` turns the
    `IntegrityError` into the same refusal.
    """
    from .services import pass_service

    row = await pass_service.activate(user_id=current_user.id, pass_id=pass_id)
    return _pass_item(row)


@router.post("/passes/checkout", response_model=models.CheckoutResponse)
async def create_pass_checkout(
    body: models.PassCheckoutRequest,
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """Start a one-time checkout for a pass or the voice pack (§5.7.1, Decision R).

    **`mode: payment`, never a subscription** — a pass is bought once, held as inventory, and activated
    later. The Stripe rail serves USD, Paystack serves NGN; the stores verify a receipt instead and do
    not come through here.

    **The voice pack requires an active Plus entitlement to buy** (Decision R): its whole purpose is a
    subscriber out of minutes who cannot activate a pass, so a learner with no entitlement is refused
    `403 VOICE_PACK_REQUIRES_PLUS` at this boundary rather than after paying. The Term Pass is NGN-only,
    so a Stripe request for it is refused with the door it belongs to.
    """
    if body.product_id == "plus_voice_30":
        resolved = await entitlement_service.resolve(current_user.id)
        if resolved.tier != "plus":
            raise MaigieError(
                message=(
                    "The voice pack is an add-on to Maigie Plus. Start a subscription or activate a "
                    "pass first, then top up your voice minutes."
                ),
                status_code=status.HTTP_403_FORBIDDEN,
                code="VOICE_PACK_REQUIRES_PLUS",
            )

    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    success_url = f"{base_url}/passes/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/passes/cancel"

    if body.provider == "stripe":
        from .services import stripe_service

        try:
            result = await stripe_service.create_one_time_checkout(
                current_user, body.product_id, success_url, cancel_url
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return models.CheckoutResponse(**result)

    # provider == "paystack": the NGN one-time rail. Returns the authorization URL as `url`, and the
    # transaction reference as `session_id` — the reference is what the `charge.success` webhook and
    # `PlusPurchase` key idempotency on.
    from .services import paystack_service

    try:
        result = await paystack_service.initialize_pass_transaction(
            current_user, body.product_id, success_url, cancel_url
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return models.CheckoutResponse(
        session_id=result.get("reference") or "",
        url=result.get("authorization_url"),
        modified=False,
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


@router.post(
    "/purchases/google-play/verify",
    response_model=models.GooglePlayProductVerifyResponse,
)
async def google_play_verify_product(
    body: models.GooglePlayProductVerifyRequest, current_user: CurrentUser
):
    """Verify a one-time Google Play pass/voice purchase and grant it.

    The mobile client sends the `purchaseToken` Play Billing returned; the server verifies it with the
    Play Developer API, persists a `PlusPurchase` and grants an inventory pass (Decision G, A) — or,
    for the voice pack, credits seconds. Idempotent on the token: a replay grants nothing, and a token
    already bound to another learner answers `409 PURCHASE_ALREADY_CLAIMED`. A voice-pack purchase by a
    learner without an active entitlement is refused `403 VOICE_PACK_REQUIRES_PLUS` (Decision R).

    This is also iOS's and Android's restore path for a finished consumable that StoreKit/Play Billing
    will not return — inventory is read from `GET /billing/passes`, not the store.
    """
    from .services.google_play_service import verify_product_purchase

    try:
        result = await verify_product_purchase(
            user_id=current_user.id,
            product_id=body.productId,
            purchase_token=body.purchaseToken,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return models.GooglePlayProductVerifyResponse(**result)


@router.post("/purchases/apple/verify", response_model=models.AppleVerifyResponse)
async def apple_verify_product(
    body: models.AppleVerifyRequest,
    current_user: CurrentUser,
    settings: Settings = Depends(get_settings),
):
    """Verify a one-time Apple (StoreKit 2) pass/voice purchase and grant it.

    The app sends the signed transaction JWS; the server verifies it against Apple's root CA, persists
    a `PlusPurchase` and grants an inventory pass (Decision G, A) — or credits the voice pack. Idempotent
    on Apple's `transactionId`; a voice-pack purchase without an active entitlement is refused `403
    VOICE_PACK_REQUIRES_PLUS` (Decision R). Also the iOS restore path for a finished consumable, which
    StoreKit will not return — inventory is read from `GET /billing/passes`.
    """
    if not settings.APPLE_ROOT_CA_DIR:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Apple purchase rail is not configured.",
        )

    from .services import apple_service

    try:
        result = await apple_service.verify_transaction(
            user_id=current_user.id,
            signed_transaction_info=body.signedTransactionInfo,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        # A signature that does not verify is a bad request, not a server fault. The library raises its
        # own VerificationException; catching broadly here keeps an unverifiable receipt a 400 rather
        # than leaking a 500.
        logger.warning("Apple transaction verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Apple transaction could not be verified.",
        )
    return models.AppleVerifyResponse(**result)


# ===========================================================================
# Credits
# ===========================================================================
#
# The credit-pack catalog and purchase endpoints are gone with the product. What is left is
# history: it describes transactions that really happened.
#
# `POST /admin/credits/adjust` is gone too, and it is the one deletion here that removes a
# capability rather than a dead product. It moved a figure in `purchasedCreditsBalance`,
# which Phase 3 dropped. Usage is now a window that refills on its own, so there is no
# balance for support to top up; pointing this at the window instead would let support hand
# out an allowance that expires in under five hours, which looks like help and is gone
# before the ticket closes. The replacement is a granted pass, and it arrives with the pass
# rails.


@router.get("/purchases", response_model=models.PaginatedPurchaseHistory)
async def get_purchase_history(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """Paginated purchase history: every pass and subscription the learner bought.

    Reads `PlusPurchase` (Decision H). Was `/credits/purchases` over `CreditPurchaseTransaction`, which
    is dropped along with `CreditPack` in migration 072 — credit packs are the withdrawn product and
    nobody ever bought one. Renamed to `/purchases` because it is no longer about credits, matching §8.

    The support surface for "what did I pay you", so it returns failed and refunded purchases too.
    """
    return await credit_service.get_purchase_history(
        user_id=current_user.id, page=page, page_size=pageSize
    )


# ===========================================================================
# Points (§6.9, Decision O)
# ===========================================================================


def _points_ledger_item(row) -> models.PointsLedgerItem:
    return models.PointsLedgerItem(
        id=row.id,
        points=row.points,
        kind=row.kind,
        expiresAt=row.expires_at,
        sourceRef=row.source_ref,
        note=row.note,
        createdAt=row.created_at,
    )


@router.get("/points", response_model=models.PointsBalanceResponse)
async def get_points(current_user: CurrentUser):
    """The learner's points wallet: balance, what it can buy, when the next batch expires, and the ledger.

    `redeemable` is computed from the balance so a client offers only passes the learner can actually
    take — it never shows a pass it will then refuse. `history` is the whole ledger, newest first,
    because the wallet explains its own number (§6.9): the referral that earned points, the pass that
    spent them, the grant that expired.
    """
    from .services import points_service

    bal = await points_service.balance(current_user.id)
    entries = await points_service.history(current_user.id)
    return models.PointsBalanceResponse(
        balance=bal.balance,
        nextExpiryAt=bal.next_expiry_at,
        nextExpiryPoints=bal.next_expiry_points,
        redeemable=bal.redeemable,
        history=[_points_ledger_item(row) for row in entries],
    )


@router.post("/points/redeem", response_model=models.PlusPassItem)
async def redeem_points(body: models.RedeemPointsRequest, current_user: CurrentUser):
    """Spend points on a pass. Returns the inventory pass, ready to activate.

    **Only a pass.** `productId` is constrained to the two pass ids at the schema, and the service
    refuses anything not in `POINTS_COST` before reading a thing — the subscription is unreachable from
    here by construction, not by a check (§6.9). Not enough points held is `409 INSUFFICIENT_POINTS`,
    the code a client turns into "you need N more"; a non-pass id is `422`.

    The pass arrives in inventory with `source='points'` and no purchase behind it (Decision O), so it
    activates through the same rail a bought pass uses — the learner starts its clock when they want it.
    """
    from .services import points_service

    new_pass = await points_service.redeem(user_id=current_user.id, product_id=body.productId)
    return _pass_item(new_pass)


# ===========================================================================
# Referrals
# ===========================================================================
#
# `/referrals/claimable` and `/referrals/claim` are gone with the token grant they served. A referral
# is no longer a claim against a daily limit: it is points, earned when a referred learner has studied
# on seven distinct days, redeemable for passes and nothing else (Decision O). The reward lives at
# `GET /billing/points`; what remains here is the standing — the code to share and who it brought.


@router.get("/referrals", response_model=models.ReferralsResponse)
async def get_referrals(current_user: CurrentUser):
    """The learner's referral code and how many learners it has brought.

    The reward itself — points earned when a referred learner stays — is read from `GET /billing/points`,
    not reported here. `referral_service.get_referral_stats` survives from before; its retired token
    totals are dropped from the response shape (§6.9).
    """
    from .services import referral_service

    stats = await referral_service.get_referral_stats(current_user.id)
    return models.ReferralsResponse(
        referralCode=stats["referralCode"],
        totalReferrals=stats["totalReferrals"],
    )


# ===========================================================================
# Ads (Rewarded Video) — withdrawn
# ===========================================================================
#
# `/ads/stats` and `/ads/reward` are gone. See `services/credit_service.py` for the
# reasoning: the reward was an invisible daily credit-limit increase, and nothing in the
# product asks a learner to watch an advertisement.
