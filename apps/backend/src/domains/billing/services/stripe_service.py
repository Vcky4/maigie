"""
Subscription service for Stripe integration.

This module handles all Stripe subscription operations including:
- Creating checkout sessions
- Managing subscriptions
- Handling webhook events

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime
from typing import Any

import stripe
from sqlalchemy import select

from src.config import Settings, get_settings
from src.domains.billing.repository import billing_repo
from src.domains.identity.db_models import User
from src.domains.identity.repository import IdentityRepository
from src.shared.database import get_session_factory
from src.shared.exceptions import DeprecatedPlanError
from src.shared.infrastructure.email import send_subscription_success_email

from ..models import PlanCatalogResponse, PlanItem

logger = logging.getLogger(__name__)

# Initialize Stripe
settings = get_settings()
stripe.api_key = settings.STRIPE_SECRET_KEY

# Plan identifiers accepted at the **subscription** checkout surface.
#
# There is one personal subscription — Maigie Plus monthly — plus the legacy
# ``maigie_plus_monthly`` slug, kept so a shipped client that still sends it does not
# break. The two space-scoped entries are unchanged and out of scope.
#
# The two Plus passes are deliberately **absent**. A pass is a one-time product bought
# with ``mode: payment``; it is not a subscription and does not belong on this surface.
# It appears in the catalog so clients can price and display it, and it becomes
# purchasable when the one-time checkout lands.
PLAN_IDS = (
    "maigie_plus_monthly",
    "plus_monthly",
    "circle_plan_monthly",
    "plus_seat_add_on_monthly",
)

# Product identifiers in the catalog that are not subscriptions. Kept separate from
# ``PLAN_IDS`` so that asking for one at the subscription checkout is a clear, specific
# refusal rather than a generic "invalid plan_id".
PASS_PRODUCT_IDS = ("plus_pass_5h", "plus_pass_7d")

# One-time products sold on the Stripe (USD) rail, mapped to the price-id setting each reads.
# `plus_pass_term` is **absent by design** (§5.7.1): it is NGN-only, so its rails are Paystack on web
# and the stores on mobile. A USD price for it would put an unbuyable product one API call from sale.
ONE_TIME_PRICE_SETTINGS = {
    "plus_pass_5h": "STRIPE_PRICE_ID_PLUS_PASS_5H",
    "plus_pass_7d": "STRIPE_PRICE_ID_PLUS_PASS_7D",
    "plus_voice_30": "STRIPE_PRICE_ID_PLUS_VOICE_30",
}

# Plan identifiers that have been removed from the active catalog.
# Creation requests referencing these are rejected with HTTP 410.
#
# These ids stay reachable on purpose. A plan removed from the catalog is not the same
# thing as a plan that never existed, and a client holding a stale id deserves to be told
# which. That is only possible if the request model still accepts the id — see the
# ``PlanId`` literal in ``billing/models.py``, which lists them for exactly this reason.
DEPRECATED_PLAN_IDS = {
    # Yearly Plus is withdrawn. Existing PREMIUM_YEARLY subscribers are grandfathered and
    # keep renewing — the Stripe price id survives in config for that — but the product is
    # not sold again. Withdrawing it is consistent with retiring every other multi-tier
    # personal product; the catalog is four entries, not five.
    "plus_yearly": (
        "PLUS_YEARLY_PLAN_REMOVED",
        "Yearly Maigie Plus has been withdrawn. Maigie Plus is $4.99/month, "
        "and the 5-hour and 7-day Plus passes are available if you would "
        "rather not subscribe.",
    ),
    "maigie_plus_yearly": (
        "PLUS_YEARLY_PLAN_REMOVED",
        "Yearly Maigie Plus has been withdrawn. Maigie Plus is $4.99/month, "
        "and the 5-hour and 7-day Plus passes are available if you would "
        "rather not subscribe.",
    ),
    "study_circle_monthly": (
        "STUDY_CIRCLE_PLAN_REMOVED",
        "The Study Circle plan has been retired. Please subscribe to "
        "Maigie Plus and, if you own a Circle, upgrade it with the new "
        "Circle Plan.",
    ),
    "study_circle_yearly": (
        "STUDY_CIRCLE_PLAN_REMOVED",
        "The Study Circle plan has been retired. Please subscribe to "
        "Maigie Plus and, if you own a Circle, upgrade it with the new "
        "Circle Plan.",
    ),
    "squad_monthly": (
        "SQUAD_PLAN_REMOVED",
        "The Squad plan has been retired. Please subscribe to Maigie "
        "Plus or use the new Circle Plan.",
    ),
    "squad_yearly": (
        "SQUAD_PLAN_REMOVED",
        "The Squad plan has been retired. Please subscribe to Maigie "
        "Plus or use the new Circle Plan.",
    ),
}


def _is_first_plus_purchase(user: User) -> bool:
    """Return True when this user has never had a Maigie Plus subscription.

    Used to decide whether to grant the Maigie Plus trial. A user is treated as a
    first-time Plus subscriber when their stored ``Tier`` is ``FREE`` and they have
    no record of a paid plan in either provider.
    """
    if str(user.tier or "FREE") != "FREE":
        return False
    return not (user.stripe_subscription_id or user.paystack_subscription_code)


def _voice_minutes_note(voice_seconds: int) -> str:
    """The voice figure as a learner reads it: "15 minutes of live voice tutoring".

    **Reads the voice allowance, and used to divide the unit window by the voice rate.** That division
    was the honest description of a meter that drew voice from the same allowance as text, and §6.3
    replaced that meter: voice has its own counter now, so the figure is the allowance rather than a
    quotient. Two things improve. The number stops being an artefact — a pass advertising "about 15
    minutes" because 3 000 units ÷ 200 happens to equal 15 is a promise nothing was enforcing, where
    600 seconds on `voiceSecondsRemaining` is a promise a counter keeps. And the word "about" goes,
    because there is nothing approximate left: the learner gets exactly the minutes named.

    A tier with no voice returns the capability statement rather than "0 minutes". A free learner has
    not run out of anything, and §6.3 makes voice the clearest thing a pass sells — so the copy has to
    say what it is rather than what it is not.
    """
    if voice_seconds <= 0:
        return "no live voice tutoring — that's a Plus capability"
    minutes = voice_seconds // 60
    return f"{minutes} minutes of live voice tutoring"


def get_active_plan_catalog() -> PlanCatalogResponse:
    """Return the active product catalog.

    Six entries: four ``personal`` products and the two space-scoped ones.

    Personal is ``free``, the two Plus passes, and ``plus_monthly``. Yearly Plus,
    the three credit packs and the ``study_circle_*`` / ``squad_*`` tiers are all
    withdrawn; the deprecated ids answer 410 rather than disappearing silently.

    Every price, trial length and usage equivalent a client displays is served from
    here. Nothing on any surface may hold a second copy of these numbers — the four
    repositories held nine copies of the subscription price alone, and they disagreed.

    ``usage_note`` exists because "5 hours of Plus" invites the reader to assume five hours
    of live voice tutoring, which costs about $6.00 to serve against a pass that nets
    $0.75. Every note therefore says that voice is allowanced rather than unlimited.

    **The figures are back, and they are derived rather than typed.** Phase 2a stripped them
    because they promised the §6.3 window allowances against a meter that implemented a
    monthly token cap — for ``plus_monthly``, roughly nineteen times more than the live meter
    funded. Phase 3 is the change that makes them true, so it is the change that restores
    them: ``_voice_minutes_note`` computes each one from ``Entitlement.window_allowance`` and
    the configured voice rate, so a note cannot outlive the allowance it describes. Retyping
    the number is what let the last set drift, and a customer-facing figure no code can honour
    reads as a commitment.

    **Only voice carries a count.** Voice is the scarce thing — a minute costs about 200 units
    against a free window of 500 — and it is the promise a learner can check against a stopwatch.
    Chat is described qualitatively on purpose, and not for lack of an allowance to quote from:
    at the current rate card a free window funds about 12 chat turns on Flash-Lite while a Plus
    window funds about 22 on Flash, because Plus buys a dearer model as well as a larger
    allowance. Publishing both counts side by side would invite a comparison that understates
    Plus by nearly the whole of what it sells, and answering it would mean either quoting Free's
    figure against Plus's model or hiding the model difference. Recorded as Open Question 4.
    """
    from src.domains.billing.services import entitlement_service as ent

    cfg = get_settings()
    plans = [
        PlanItem(
            id="free",
            name="Free",
            scope="personal",
            price_cents=0,
            interval="none",
            description=(
                "Everything Maigie does, at a standard level: notes, flashcards, "
                "practice, study plans, courses and weekly reflections."
            ),
            usage_note=(
                "Standard model quality, and " f"{_voice_minutes_note(ent.VOICE_SECONDS_FREE)}."
            ),
        ),
        PlanItem(
            id="plus_pass_5h",
            name="5-Hour Plus Pass",
            scope="personal",
            price_cents=cfg.PRICE_CENTS_PLUS_PASS_5H,
            # Not "none" and not "month": a pass is bought once and runs once. Clients group
            # the catalog on this field, and a pass belongs beside the other pass rather
            # than beside the subscription.
            interval="one_time",
            description=(
                "Full Maigie Plus for 5 hours, starting when you activate it. "
                "Hold it as long as you like; it does not renew."
            ),
            usage_note=(
                "Every Plus feature for the 5 hours, including "
                f"{_voice_minutes_note(ent.VOICE_SECONDS_PASS_5H)} — "
                "an allowance inside the 5 hours, not 5 unbroken hours of voice."
            ),
            # Listed so clients and generated types can be built against the real shape;
            # the one-time checkout that sells it arrives in Phase 5. Until then a Buy
            # button would answer 400.
            purchasable=False,
        ),
        PlanItem(
            id="plus_pass_7d",
            name="7-Day Plus Pass",
            scope="personal",
            price_cents=cfg.PRICE_CENTS_PLUS_PASS_7D,
            interval="one_time",
            description=(
                "Full Maigie Plus for 7 days, starting when you activate it. "
                "A study week. It does not renew."
            ),
            usage_note=(
                "Every Plus feature for the 7 days, including "
                f"{_voice_minutes_note(ent.VOICE_SECONDS_PASS_7D)} "
                "for the week."
            ),
            purchasable=False,  # Phase 5, as above.
        ),
        PlanItem(
            id="plus_monthly",
            name="Maigie Plus",
            scope="personal",
            price_cents=cfg.PRICE_CENTS_PLUS_MONTHLY,
            interval="month",
            trial_days=cfg.TRIAL_DAYS_MAIGIE_PLUS,
            description=(
                "A stronger model for chat, quizzes, lessons and documents; adaptive "
                "practice and plans; deeper reflections; and every document format, "
                "in your personal workspace."
            ),
            # Was "Advanced models throughout", which was unenforceable in both directions: the model
            # allowlist let Free use the Plus model on chat, and on the other 26 call sites there is
            # still no split at all. Decision P confines the split to operations above 500 units, so
            # the honest claim names the surfaces rather than saying "throughout" — and the surfaces
            # it names are the ones a learner would notice.
            usage_note=(
                "A stronger model where it shows — chat, quizzes, lessons, documents "
                "and your growth write-ups — plus "
                f"{_voice_minutes_note(ent.VOICE_SECONDS_PLUS_MONTHLY)} "
                "a month."
            ),
        ),
        PlanItem(
            id="circle_plan_monthly",
            name="Circle Plan",
            scope="circle",
            price_cents=cfg.PRICE_CENTS_CIRCLE_PLAN_MONTHLY,
            interval="month",
            trial_days=cfg.TRIAL_DAYS_CIRCLE_PLAN,
            description="Per-Circle plan with 4 included Plus seats and premium Circle features.",
        ),
        PlanItem(
            id="plus_seat_add_on_monthly",
            name="Plus Seat Add-on",
            scope="add_on",
            price_cents=cfg.PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY,
            interval="month",
            description=(
                "Adds one Plus seat to a Circle. Owners and admins can "
                "assign and reassign seats freely."
            ),
        ),
    ]
    return PlanCatalogResponse(plans=plans)


def assert_plan_id_is_active(plan_id: str) -> None:
    """Reject creation requests for deprecated plan ids.

    Implements Requirements 1.9 and 2.1: any subscription creation
    request whose plan id maps to ``STUDY_CIRCLE_*`` or ``SQUAD_*`` must
    fail with HTTP 410 and the corresponding ``*_PLAN_REMOVED`` code.
    """
    if plan_id in DEPRECATED_PLAN_IDS:
        code, message = DEPRECATED_PLAN_IDS[plan_id]
        raise DeprecatedPlanError(code=code, message=message)


def get_price_id_and_trial_days(plan_id: str, *, user: User | None = None) -> tuple[str, int]:
    """
    Get Stripe price ID and trial days for a plan.

    Rejects deprecated plan ids (``plus_yearly`` / ``study_circle_*`` / ``squad_*``)
    with ``DeprecatedPlanError``. The Maigie Plus trial is granted only on a user's
    first PLUS purchase; pass ``user`` to enforce this. When ``user`` is omitted
    (existing call sites that handle trial logic separately) the full configured trial
    length is returned.

    Args:
        plan_id: Active plan identifier.
        user: Optional purchasing user, used to suppress repeat trials.

    Returns:
        (price_id, trial_days)

    Raises:
        DeprecatedPlanError: If plan_id refers to a removed tier.
        ValueError: If plan_id is a pass, or is otherwise invalid.
    """
    assert_plan_id_is_active(plan_id)

    if plan_id in PASS_PRODUCT_IDS:
        # Named explicitly rather than falling through to "invalid plan_id", because a
        # client asking for a pass here is not confused about the product — it is using
        # the wrong endpoint. A pass is a one-time charge and has no trial, no renewal
        # and no subscription to modify.
        raise ValueError(
            f"'{plan_id}' is a one-time Plus pass, not a subscription. "
            f"Passes are purchased through the one-time checkout, not "
            f"/subscriptions/checkout."
        )

    plus_trial = settings.TRIAL_DAYS_MAIGIE_PLUS
    if user is not None and not _is_first_plus_purchase(user):
        plus_trial = 0

    if plan_id in ("maigie_plus_monthly", "plus_monthly"):
        return settings.STRIPE_PRICE_ID_MONTHLY, plus_trial
    if plan_id == "circle_plan_monthly":
        # The Circle Plan trial is owned by the Circle billing service.
        # Personal-checkout surface should not honor it; return 0 here.
        return settings.STRIPE_PRICE_ID_CIRCLE_PLAN_MONTHLY, 0
    if plan_id == "plus_seat_add_on_monthly":
        return settings.STRIPE_PRICE_ID_PLUS_SEAT_ADD_ON_MONTHLY, 0
    raise ValueError(f"Invalid plan_id: {plan_id}. " f"Must be one of: {', '.join(PLAN_IDS)}")


def _price_id_to_tier(price_id: str) -> str:
    """Map a Stripe price ID to a tier enum value.

    **Only tiers the resolver honours.** `PREMIUM_MONTHLY` is the one tier still on sale, and it is
    the only one `entitlement_service.PLUS_TIERS` grants Plus for, so it is the only one this
    function may produce for a paid price.

    Until Phase 2b this also mapped the yearly price to ``PREMIUM_YEARLY`` and four retired price
    ids to ``STUDY_CIRCLE_*`` / ``SQUAD_*``, on the reasoning that a webhook for a legacy
    subscription should still resolve its source tier. That reasoning stopped holding when the
    resolver narrowed: writing a tier string nothing grants Plus for means a renewal that bills a
    learner and entitles them to nothing. A writer that can produce a value the resolver denies is
    the defect, not the mapping table.

    Safe because there is nothing left to map — `scripts/count_legacy_commercial_state.py` counted
    zero users on those tiers and zero subscription identifiers of any kind on 2026-09-01, and all
    five products are withdrawn from sale, so no renewal for one can arrive. An unrecognised price
    id falls to ``FREE``, which is also what a withdrawn price now returns: refusing to invent an
    entitlement is the conservative direction for an input we no longer expect.
    """
    if price_id == settings.STRIPE_PRICE_ID_MONTHLY:
        return "PREMIUM_MONTHLY"
    return "FREE"


def _assert_price_id_is_active(price_id: str) -> None:
    """Reject creation requests against a deprecated Stripe price ID.

    Any subscription creation or plan switch that targets a withdrawn price must fail
    with HTTP 410: only ``PREMIUM_YEARLY`` remains guarded here.

    Yearly is here as well as in ``DEPRECATED_PLAN_IDS`` because the two functions guard
    different doors. ``assert_plan_id_is_active`` guards a fresh checkout, which arrives
    as a plan id; this one guards ``modify_existing_subscription``, which arrives as a
    price id. A monthly subscriber switching to yearly is a new purchase of a withdrawn
    product, and would otherwise slip through. Existing yearly subscribers are unaffected
    — nothing here runs on a renewal, which is why the price id survives in config.

    The Study Circle and Squad price-id branches were removed with their config settings
    (§5.7.3 / §5.1): zero subscribers on those tiers (Phase 2b) means no price-id webhook
    or plan switch for one can arrive. The retired *plan ids* still answer 410 through
    ``assert_plan_id_is_active`` / ``DEPRECATED_PLAN_IDS``, which is plan-id keyed and
    needs no config setting.
    """
    if price_id and price_id == settings.STRIPE_PRICE_ID_YEARLY:
        raise DeprecatedPlanError(
            code="PLUS_YEARLY_PLAN_REMOVED",
            message="Yearly Maigie Plus has been withdrawn.",
        )


async def get_or_create_stripe_customer(user: User) -> str:
    """
    Get existing Stripe customer ID or create a new customer.

    Args:
        user: User model instance

    Returns:
        Stripe customer ID
    """
    if user.stripe_customer_id:
        return user.stripe_customer_id

    # Create new Stripe customer
    customer = stripe.Customer.create(
        email=user.email,
        name=user.name,
        metadata={"user_id": user.id},
    )

    # Update user with Stripe customer ID
    identity_repo = IdentityRepository()
    await identity_repo.update(user.id, {"stripeCustomerId": customer.id})

    return customer.id


def _is_upgrade(current_tier: str, new_price_id: str) -> bool:
    """
    Determine if changing from current tier to new price is an upgrade.

    Args:
        current_tier: Current tier (FREE, PREMIUM_*, STUDY_CIRCLE_*, SQUAD_*)
        new_price_id: New Stripe price ID

    Returns:
        True if upgrade, False if downgrade
    """
    # Tier hierarchy: FREE < Maigie Plus < Study Circle < Squad (monthly < yearly within each)
    tier_order = {
        "FREE": 0,
        "PREMIUM_MONTHLY": 1,
        "PREMIUM_YEARLY": 2,
        "STUDY_CIRCLE_MONTHLY": 3,
        "STUDY_CIRCLE_YEARLY": 4,
        "SQUAD_MONTHLY": 5,
        "SQUAD_YEARLY": 6,
    }

    current_order = tier_order.get(current_tier, 0)
    new_tier = _price_id_to_tier(new_price_id)
    new_order = tier_order.get(new_tier, 0)

    return new_order > current_order


async def modify_existing_subscription(user: User, new_price_id: str) -> dict:
    """
    Modify an existing subscription (upgrade or downgrade).

    Args:
        user: User model instance with active subscription
        new_price_id: New Stripe price ID to switch to

    Returns:
        Updated subscription information
    """
    if not user.stripe_subscription_id:
        raise ValueError("User does not have an active subscription")

    # Reject modification target tiers that have been retired
    # (Requirements 1.9, 2.1).
    _assert_price_id_is_active(new_price_id)

    # Retrieve current subscription
    subscription = stripe.Subscription.retrieve(
        user.stripe_subscription_id, expand=["items.data.price"]
    )

    # Check if subscription is active (not canceled, past_due, etc.)
    subscription_status = (
        subscription.status if hasattr(subscription, "status") else subscription.get("status")
    )
    if subscription_status not in ["active", "trialing"]:
        raise ValueError(
            f"Cannot modify subscription with status: {subscription_status}. "
            "Subscription must be active or trialing."
        )

    # Get current price ID
    # Handle both object and dict formats for items (same pattern as update_user_subscription_from_stripe)
    current_price_id = None
    subscription_item_id = None

    try:
        # Convert Stripe object to dict for consistent access
        if hasattr(subscription, "to_dict"):
            sub_dict = subscription.to_dict()
        elif isinstance(subscription, dict):
            sub_dict = subscription
        else:
            sub_dict = None

        if sub_dict:
            # Access as dict
            items = sub_dict.get("items", {})
            if isinstance(items, dict) and items.get("data") and len(items["data"]) > 0:
                current_price_id = items["data"][0].get("price", {}).get("id")
                subscription_item_id = items["data"][0].get("id")
            elif isinstance(items, list) and len(items) > 0:
                current_price_id = items[0].get("price", {}).get("id")
                subscription_item_id = items[0].get("id")
        else:
            # Try direct attribute access as fallback
            items = getattr(subscription, "items", None)
            if items:
                if hasattr(items, "data") and items.data and len(items.data) > 0:
                    current_price_id = items.data[0].price.id
                    subscription_item_id = items.data[0].id
                elif isinstance(items, list) and len(items) > 0:
                    current_price_id = items[0].price.id
                    subscription_item_id = items[0].id
    except (AttributeError, KeyError, IndexError, TypeError) as e:
        logger.warning(f"Could not extract subscription items: {e}")
        raise ValueError(f"Could not retrieve current subscription details: {e}")

    if not current_price_id or not subscription_item_id:
        raise ValueError("Could not retrieve current subscription details")

    # Check if it's the same price
    if current_price_id == new_price_id:
        raise ValueError("User is already subscribed to this plan")

    # Determine if upgrade or downgrade
    current_tier = str(user.tier) if user.tier else "FREE"
    is_upgrade = _is_upgrade(current_tier, new_price_id)

    # Check if we're changing billing intervals (monthly <-> yearly)
    # Retrieve both prices to check their intervals
    current_price_obj = stripe.Price.retrieve(current_price_id)
    new_price_obj = stripe.Price.retrieve(new_price_id)

    current_interval = (
        current_price_obj.recurring.get("interval") if current_price_obj.recurring else None
    )
    new_interval = new_price_obj.recurring.get("interval") if new_price_obj.recurring else None
    is_interval_change = current_interval != new_interval

    # Prepare subscription modification parameters
    current_period_end = subscription.current_period_end

    if is_upgrade:
        if is_interval_change:
            # Upgrade with interval change (e.g., monthly to yearly)
            # Stripe doesn't allow "unchanged" for interval changes
            # Charge prorated now, billing cycle resets to now
            modified_subscription = stripe.Subscription.modify(
                user.stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="create_prorations",  # Charge prorated amount now
                billing_cycle_anchor="now",  # Must use "now" for interval changes
                metadata={
                    "user_id": user.id,
                    "upgrade": "true",
                    "interval_change": "true",
                },
            )
        else:
            # Upgrade within same interval (e.g., free to monthly, or price change)
            # Charge now (prorated), billing cycle unchanged
            modified_subscription = stripe.Subscription.modify(
                user.stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="create_prorations",  # Charge prorated amount now
                billing_cycle_anchor="unchanged",  # Keep same billing cycle
                metadata={"user_id": user.id, "upgrade": "true"},
            )
    else:
        if is_interval_change:
            # Downgrade with interval change (e.g., yearly to monthly)
            # Stripe doesn't allow "unchanged" for interval changes
            # Schedule change for period end using subscription schedule
            # For now, we'll change immediately but charge at period end
            # Note: This is a limitation - we can't perfectly schedule interval changes
            modified_subscription = stripe.Subscription.modify(
                user.stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="none",  # Don't charge until next billing date
                billing_cycle_anchor="now",  # Must use "now" for interval changes
                metadata={
                    "user_id": user.id,
                    "downgrade": "true",
                    "interval_change": "true",
                },
            )
        else:
            # Downgrade within same interval
            # Charge at next billing date, changes take effect at period end
            modified_subscription = stripe.Subscription.modify(
                user.stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="none",  # Don't charge until next billing date
                billing_cycle_anchor="unchanged",  # Changes take effect at period end
                metadata={"user_id": user.id, "downgrade": "true"},
            )

    # Update user subscription data
    await update_user_subscription_from_stripe(modified_subscription.id)

    return {
        "subscription_id": modified_subscription.id,
        "status": modified_subscription.status,
        "is_upgrade": is_upgrade,
        "current_period_end": datetime.fromtimestamp(modified_subscription.current_period_end),
    }


async def create_checkout_session(
    user: User,
    price_id: str,
    success_url: str,
    cancel_url: str,
    trial_days: int = 0,
) -> dict:
    """
    Create a Stripe checkout session for subscription.

    If user already has a subscription, this will modify it instead of creating a new one.

    Args:
        user: User model instance
        price_id: Stripe price ID (monthly or yearly)
        success_url: URL to redirect after successful payment
        cancel_url: URL to redirect if user cancels

    Returns:
        Checkout session object or modification result
    """
    # Reject creation against retired tiers (Requirements 1.9, 2.1).
    _assert_price_id_is_active(price_id)

    customer_id = await get_or_create_stripe_customer(user)

    # Check if user already has an active subscription
    # First check database, then verify with Stripe
    if user.stripe_subscription_id:
        logger.info(
            f"User {user.id} has subscription ID {user.stripe_subscription_id} in database, "
            f"attempting to modify instead of creating new checkout"
        )

        # First check if the subscription is actually active/trialing on Stripe
        try:
            existing_sub = stripe.Subscription.retrieve(user.stripe_subscription_id)
            existing_status = (
                existing_sub.status
                if hasattr(existing_sub, "status")
                else existing_sub.get("status")
            )

            if existing_status in ("canceled", "incomplete_expired"):
                # Subscription is fully ended — clear it and create a fresh checkout
                logger.info(
                    f"User {user.id} has a {existing_status} subscription "
                    f"{user.stripe_subscription_id}. Clearing stale reference."
                )
                identity_repo = IdentityRepository()
                await identity_repo.update(
                    user.id,
                    {
                        "stripeSubscriptionId": None,
                        "stripeSubscriptionStatus": None,
                        "stripePriceId": None,
                        "tier": "FREE",
                        "subscriptionCurrentPeriodStart": None,
                        "subscriptionCurrentPeriodEnd": None,
                    },
                )
                user.stripe_subscription_id = None
                # Fall through to create new checkout below
            else:
                # Subscription exists and is modifiable — try to modify
                try:
                    result = await modify_existing_subscription(user, price_id)
                    logger.info(
                        f"Successfully modified subscription for user {user.id}: "
                        f"upgrade={result['is_upgrade']}, subscription_id={result['subscription_id']}"
                    )
                    return {
                        "session_id": result["subscription_id"],
                        "url": None,
                        "modified": True,
                        "is_upgrade": result["is_upgrade"],
                        "current_period_end": result["current_period_end"].isoformat(),
                    }
                except ValueError as e:
                    if "already subscribed to this plan" in str(e).lower():
                        logger.info(
                            f"User {user.id} already subscribed to requested plan. "
                            "Syncing subscription data from Stripe."
                        )
                        updated_user = await update_user_subscription_from_stripe(
                            user.stripe_subscription_id
                        )
                        if updated_user:
                            user = updated_user
                        return {
                            "session_id": user.stripe_subscription_id,
                            "url": None,
                            "modified": False,
                            "is_upgrade": False,
                            "current_period_end": (
                                user.subscription_current_period_end.isoformat()
                                if user.subscription_current_period_end
                                else None
                            ),
                        }
                    logger.warning(f"Cannot modify subscription for user {user.id}: {e}")
                    raise
                except stripe.error.StripeError as e:
                    logger.error(
                        f"Stripe error modifying subscription for user {user.id}: {e}",
                        exc_info=True,
                    )
                    raise ValueError(f"Failed to modify subscription: {str(e)}")
                except Exception as e:
                    logger.error(
                        f"Unexpected error modifying subscription for user {user.id}: {e}",
                        exc_info=True,
                    )
                    raise ValueError(f"Failed to modify subscription: {str(e)}")
        except stripe.error.StripeError as e:
            # Can't retrieve subscription — clear stale reference
            logger.warning(
                f"Could not retrieve subscription {user.stripe_subscription_id} for user {user.id}: {e}. "
                "Clearing stale reference."
            )
            identity_repo = IdentityRepository()
            await identity_repo.update(
                user.id,
                {
                    "stripeSubscriptionId": None,
                    "stripeSubscriptionStatus": None,
                },
            )
            user.stripe_subscription_id = None
    else:
        # Also check Stripe directly in case database is out of sync
        try:
            # List active or trialing subscriptions (trialing = in free trial period)
            subscriptions = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
            if not subscriptions.data:
                subscriptions = stripe.Subscription.list(
                    customer=customer_id, status="trialing", limit=1
                )
            if subscriptions.data and len(subscriptions.data) > 0:
                active_subscription = subscriptions.data[0]
                logger.info(
                    f"Found active Stripe subscription {active_subscription.id} for customer {customer_id}, "
                    f"but user {user.id} doesn't have it in database. Performing full synchronization."
                )

                # Perform full synchronization from Stripe
                updated_user = await update_user_subscription_from_stripe(active_subscription.id)
                if not updated_user:
                    # Fallback to manual ID update if standard sync fails
                    identity_repo = IdentityRepository()
                    await identity_repo.update(
                        user.id, {"stripeSubscriptionId": active_subscription.id}
                    )
                    user.stripe_subscription_id = active_subscription.id
                else:
                    user = updated_user

                # Check if we still need to modify (maybe sync already put them on the right tier)
                try:
                    result = await modify_existing_subscription(user, price_id)
                    logger.info(
                        f"Successfully modified subscription for user {user.id} after syncing: "
                        f"upgrade={result['is_upgrade']}, subscription_id={result['subscription_id']}"
                    )
                    return {
                        "session_id": result["subscription_id"],
                        "url": None,
                        "modified": True,
                        "is_upgrade": result["is_upgrade"],
                        "current_period_end": result["current_period_end"].isoformat(),
                    }
                except ValueError as e:
                    if "already subscribed to this plan" in str(e).lower():
                        logger.info(
                            f"User {user.id} is already subscribed to the requested plan after sync."
                        )
                        return {
                            "session_id": user.stripe_subscription_id,
                            "url": None,
                            "modified": False,
                            "is_upgrade": False,
                            "current_period_end": (
                                user.subscription_current_period_end.isoformat()
                                if user.subscription_current_period_end
                                else None
                            ),
                        }
                    # For other ValueErrors, re-raise to catch in outer block
                    raise
        except stripe.error.StripeError as e:
            logger.warning(
                f"Could not check Stripe for existing subscriptions for customer {customer_id}: {e}"
            )
        except Exception as e:
            logger.warning(
                f"Error checking Stripe subscriptions for user {user.id}: {e}",
                exc_info=True,
            )

    # No existing subscription or modification failed - create new checkout session
    subscription_data: dict = {
        "metadata": {"user_id": user.id},
    }
    if trial_days and trial_days > 0:
        subscription_data["trial_period_days"] = trial_days

    session_params: dict = {
        "customer": customer_id,
        "payment_method_types": ["card"],
        "line_items": [
            {
                "price": price_id,
                "quantity": 1,
            }
        ],
        "mode": "subscription",
        "allow_promotion_codes": True,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"user_id": user.id},
        "subscription_data": subscription_data,
    }

    session = stripe.checkout.Session.create(**session_params)

    return {
        "session_id": session.id,
        "url": session.url,
        "modified": False,
    }


async def create_one_time_checkout(
    user: User,
    product_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Create a Stripe `mode: payment` checkout for a pass or the voice pack.

    A one-time charge, **not** a subscription — no trial, no renewal, and none of the subscription-
    modification logic in `create_checkout_session` (which a pass must never run through: a learner
    already on Plus can still buy an inventory pass for later).

    The `user_id` and `product_id` travel in the session and payment-intent metadata so the
    `checkout.session.completed` webhook can attribute the payment to a learner and a product, and so a
    later `charge.refunded` (which carries the payment intent) can find the same purchase. Verification
    is the webhook itself: Stripe only fires `checkout.session.completed` with `payment_status == "paid"`
    once the money has moved.
    """
    setting_name = ONE_TIME_PRICE_SETTINGS.get(product_id)
    if setting_name is None:
        # The Term Pass lands here: it has no Stripe price on purpose. Named rather than generic so a
        # client sends it to the right rail.
        raise ValueError(
            f"'{product_id}' is not sold on the Stripe rail. The Term Pass is Nigeria-only "
            f"(Paystack and the stores); the passes and voice pack on Stripe are "
            f"{', '.join(ONE_TIME_PRICE_SETTINGS)}."
        )
    price_id = getattr(settings, setting_name)
    if not price_id:
        raise ValueError(f"Stripe price id for '{product_id}' is not configured ({setting_name}).")

    customer_id = await get_or_create_stripe_customer(user)
    metadata = {"user_id": user.id, "product_id": product_id, "product_kind": "pass"}
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        # Repeated on the PaymentIntent so a `charge.refunded` event — which carries the intent, not
        # the session — can still be attributed if the reference lookup ever needs it.
        payment_intent_data={"metadata": metadata},
    )
    return {"session_id": session.id, "url": session.url, "modified": False}


async def create_portal_session(user: User, return_url: str) -> dict:
    """
    Create a Stripe customer portal session for subscription management.

    Args:
        user: User model instance
        return_url: URL to redirect after portal session

    Returns:
        Portal session object with URL
    """
    if not user.stripe_customer_id:
        raise ValueError("User does not have a Stripe customer ID")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=return_url,
    )

    return {"url": session.url}


async def cancel_subscription(user: User) -> dict:
    """
    Cancel the user's active subscription.

    Args:
        user: User model instance

    Returns:
        Updated subscription status
    """
    if not user.stripe_subscription_id:
        raise ValueError("User does not have an active subscription")

    subscription = stripe.Subscription.modify(
        user.stripe_subscription_id,
        cancel_at_period_end=True,
    )

    # Update user subscription status
    identity_repo = IdentityRepository()
    await identity_repo.update(
        user.id,
        {
            "stripeSubscriptionStatus": subscription.status,
        },
    )

    return {
        "status": subscription.status,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
    }


async def sync_subscription_from_checkout_session(
    session_id: str, user_id: str, db_client: Any | None = None
) -> User | None:
    """
    Sync user subscription from a Stripe checkout session (e.g. after free trial signup).
    Called when user returns from checkout with session_id before webhooks may have fired.

    Args:
        session_id: Stripe checkout session ID (cs_xxx)
        user_id: ID of the current user (must own this session)
        db_client: Optional (kept for backward compat, ignored)

    Returns:
        Updated User or None if session invalid/not found
    """
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
        subscription_id = session.subscription
        if isinstance(subscription_id, str):
            sub_id = subscription_id
        elif subscription_id and hasattr(subscription_id, "id"):
            sub_id = subscription_id.id
        else:
            logger.warning(f"No subscription in checkout session {session_id}")
            return None
        updated = await update_user_subscription_from_stripe(sub_id)
        if updated and str(updated.id) != str(user_id):
            logger.warning(
                f"Checkout session {session_id} belongs to different user "
                f"({updated.id}) than requested ({user_id})"
            )
        return updated
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error syncing checkout session {session_id}: {e}")
        raise ValueError(f"Invalid checkout session: {str(e)}")
    except Exception as e:
        logger.error(f"Error syncing from checkout session: {e}")
        raise


async def update_user_subscription_from_stripe(
    subscription_id: str, db_client: Any | None = None
) -> User | None:
    """
    Update user subscription data from Stripe subscription object.

    Args:
        subscription_id: Stripe subscription ID
        db_client: Optional (kept for backward compat, ignored)

    Returns:
        Updated User object or None if not found
    """
    identity_repo = IdentityRepository()

    try:
        # Retrieve subscription with expanded items to get price information
        subscription = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])
        # Handle both object and dict formats for customer_id
        customer_id = (
            subscription.customer
            if hasattr(subscription, "customer")
            else subscription.get("customer")
        )

        # Find user by Stripe customer ID
        user = await billing_repo.find_user_by_stripe_customer(customer_id)

        if not user:
            logger.warning(f"User not found for Stripe customer: {customer_id}")
            return None

        # Determine tier based on price ID
        # Convert Stripe object to dict for consistent access
        if hasattr(subscription, "to_dict"):
            sub_dict = subscription.to_dict()
        elif isinstance(subscription, dict):
            sub_dict = subscription
        else:
            # Fallback: try to access attributes directly
            sub_dict = None

        price_id = None
        try:
            if sub_dict:
                # Access as dict
                items = sub_dict.get("items", {})
                if isinstance(items, dict) and items.get("data") and len(items["data"]) > 0:
                    price_id = items["data"][0].get("price", {}).get("id")
                elif isinstance(items, list) and len(items) > 0:
                    price_id = items[0].get("price", {}).get("id")
            else:
                # Try direct attribute access as fallback
                items = getattr(subscription, "items", None)
                if items:
                    if hasattr(items, "data") and items.data and len(items.data) > 0:
                        price_id = items.data[0].price.id
                    elif isinstance(items, list) and len(items) > 0:
                        price_id = items[0].price.id
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            logger.warning(f"Could not extract price_id from subscription: {e}")
            price_id = None

        tier = _price_id_to_tier(price_id) if price_id else "FREE"

        # If subscription is fully canceled or incomplete_expired, tier should be FREE
        # regardless of what price was attached
        sub_status_raw = (
            subscription.status if hasattr(subscription, "status") else subscription.get("status")
        )
        if sub_status_raw in ("canceled", "incomplete_expired"):
            tier = "FREE"

        # Get subscription ID and status (handle both object and dict)
        sub_id = subscription.id if hasattr(subscription, "id") else subscription.get("id")
        sub_status = (
            subscription.status if hasattr(subscription, "status") else subscription.get("status")
        )
        sub_period_start = (
            subscription.current_period_start
            if hasattr(subscription, "current_period_start")
            else subscription.get("current_period_start")
        )
        sub_period_end = (
            subscription.current_period_end
            if hasattr(subscription, "current_period_end")
            else subscription.get("current_period_end")
        )

        # Convert timestamps to datetime objects
        period_start_dt = datetime.fromtimestamp(sub_period_start) if sub_period_start else None
        period_end_dt = datetime.fromtimestamp(sub_period_end) if sub_period_end else None

        # Update user subscription data
        updated_user = await identity_repo.update(
            user.id,
            {
                "stripeSubscriptionId": sub_id,
                "stripeSubscriptionStatus": sub_status,
                "stripePriceId": price_id,
                "tier": tier,
                "paymentProvider": "stripe",
                "subscriptionCurrentPeriodStart": period_start_dt,
                "subscriptionCurrentPeriodEnd": period_end_dt,
            },
        )

        # A new billing period used to reset the month's credits here, which is why the block above
        # computed an `is_new_period` flag by comparing period starts. Phase 3 deleted
        # `reset_credits_for_period_start`: usage is a rolling 5-hour window, so no counter is keyed
        # to a billing period and a renewal has nothing to zero. The allowance a subscription buys is
        # read live from `entitlement_service.resolve` rather than copied onto the user at payment
        # time — which is what kept the two from drifting when a payment succeeded and this write did
        # not. The flag went with its only reader.

        # Send email if upgraded from FREE to Premium
        # Convert enum to string for comparison just in case
        old_tier = str(user.tier) if user.tier else "FREE"
        new_tier = str(updated_user.tier)

        paid_tier_prefixes = ("PREMIUM", "STUDY_CIRCLE", "SQUAD")
        if old_tier == "FREE" and any(new_tier.startswith(p) for p in paid_tier_prefixes):
            try:
                # Run as background task or just await (it's async)
                await send_subscription_success_email(
                    email=updated_user.email,
                    name=updated_user.name or "User",
                    tier=new_tier,
                )
            except Exception as e:
                logger.error(f"Failed to send subscription success email: {e}")

            # `track_referral_subscription` was called here and is deleted rather than ported.
            # Decision O replaces referral rewards with a points ledger in which a subscription
            # grants nothing — points come from a referred learner *studying*, on seven distinct
            # days, and they redeem into passes only. Rewarding the referrer for a payment they did
            # not make paid out on the easiest signal to fake.
            #
            # It could not have run anyway: `referral_rewards_service` held a Prisma sentinel where
            # its database used to be. Worth noting the shape of the failure, because the same shape
            # is one line above — the `except` logs "failed to track referral" and returns normally,
            # so a function that raised on every call looked like an intermittent tracking problem.

        return updated_user

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error updating subscription: {e}")
        raise
    except Exception as e:
        logger.error(f"Error updating user subscription: {e}")
        raise


async def handle_stripe_event(event: dict) -> None:
    """Route a verified Stripe event to the right handler.

    The single entry point the webhook calls, replacing a dispatch that passed the whole event where
    an event-type string was expected. Subscription events keep their existing path; the two one-time
    branches — `checkout.session.completed` for a paid pass/voice checkout, and `charge.refunded` for a
    revocation — are new. Anything else is ignored: Stripe sends far more than we act on, and an
    unhandled type is not an error.
    """
    event_type = event.get("type", "")
    data_object = (event.get("data") or {}).get("object") or {}

    if event_type.startswith("customer.subscription."):
        await handle_subscription_webhook(event_type, data_object)
    elif event_type == "checkout.session.completed":
        await _handle_checkout_completed(data_object)
    elif event_type == "charge.refunded":
        await _handle_charge_refunded(data_object)
    else:
        logger.debug("stripe: ignoring event type %s", event_type)


async def _handle_checkout_completed(session: dict) -> None:
    """Fulfil a one-time pass/voice checkout. Subscription checkouts are handled by their own events.

    Only `mode == "payment"` sessions are ours: a subscription checkout also fires this event, but its
    state is applied by the `customer.subscription.*` handler, so acting on it here would be a second,
    conflicting writer. The `payment_intent` is the idempotency key carried into `PlusPurchase`, and it
    is also what a later `charge.refunded` presents — so a refund can find exactly this purchase.
    """
    if session.get("mode") != "payment":
        return
    if session.get("payment_status") != "paid":
        logger.info(
            "stripe: checkout %s is %s, not paid — not fulfilling",
            session.get("id"),
            session.get("payment_status"),
        )
        return

    metadata = session.get("metadata") or {}
    user_id = metadata.get("user_id")
    product_id = metadata.get("product_id")
    if not user_id or not product_id:
        logger.warning(
            "stripe: one-time checkout %s carried no user_id/product_id metadata",
            session.get("id"),
        )
        return

    provider_reference = session.get("payment_intent") or session.get("id")
    from src.domains.billing.services import purchase_service

    await purchase_service.fulfill_purchase(
        user_id=user_id,
        product_id=product_id,
        provider="stripe",
        provider_reference=provider_reference,
        amount_minor=session.get("amount_total") or 0,
        currency=(session.get("currency") or "usd").upper(),
        raw_payload={"stripe_session_id": session.get("id")},
    )


async def _handle_charge_refunded(charge: dict) -> None:
    """Revoke the pass behind a refunded charge, if the charge was one of ours.

    Keyed on the `payment_intent`, which is the `PlusPurchase.providerReference` for a one-time charge.
    A subscription refund carries an intent that matches no purchase and is a harmless no-op.
    """
    payment_intent = charge.get("payment_intent")
    if not payment_intent:
        return
    from src.domains.billing.services import purchase_service

    await purchase_service.refund_purchase(provider_reference=payment_intent)


async def handle_subscription_webhook(
    event_type: str, subscription: dict, db_client: Any | None = None
) -> None:
    """
    Handle Stripe webhook events for subscriptions.

    Args:
        event_type: Stripe event type (e.g., 'customer.subscription.created')
        subscription: Stripe subscription object
        db_client: Optional (kept for backward compat, ignored)
    """
    identity_repo = IdentityRepository()

    subscription_id = subscription.get("id")
    if not subscription_id:
        logger.warning("Subscription ID not found in webhook data")
        return

    # Update user subscription data
    await update_user_subscription_from_stripe(subscription_id)

    # Handle specific event types
    if event_type == "customer.subscription.deleted":
        # Subscription was canceled - set user to FREE tier
        customer_id = subscription.get("customer")
        if customer_id:
            user = await billing_repo.find_user_by_stripe_customer(customer_id)
            if user:
                await identity_repo.update(
                    user.id,
                    {
                        "tier": "FREE",
                        "stripeSubscriptionStatus": "canceled",
                        "stripeSubscriptionId": None,
                        "stripePriceId": None,
                        "subscriptionCurrentPeriodStart": None,
                        "subscriptionCurrentPeriodEnd": None,
                    },
                )
                logger.info(f"Subscription canceled for user: {user.id}")
