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

import stripe

from prisma import Prisma
from prisma.models import User

from ..config import Settings, get_settings
from ..core.database import db
from ..schemas.subscription import (
    PlanCatalogEntry,
    PlanCatalogProductId,
    PlanCatalogResponse,
    PlanCatalogScope,
)
from ..services.credit_service import reset_credits_for_period_start
from ..services.email import send_subscription_success_email
from ..services.referral_service import track_referral_subscription
from ..utils.exceptions import DeprecatedPlanError

logger = logging.getLogger(__name__)

# Initialize Stripe
settings = get_settings()
stripe.api_key = settings.STRIPE_SECRET_KEY

# Plan identifiers accepted at the active checkout surface.
# Per Requirements 1.1, 1.6, 1.8, 1.10 and 17.9, the catalog only exposes
# the new tier set. ``maigie_plus_*`` are kept under the existing slugs to
# avoid client churn; ``plus_monthly`` / ``plus_yearly`` are the new
# user-facing aliases that map to the same Stripe prices.
PLAN_IDS = (
    "maigie_plus_monthly",
    "maigie_plus_yearly",
    "plus_monthly",
    "plus_yearly",
    "circle_plan_monthly",
    "plus_seat_add_on_monthly",
)

# Plan identifiers that have been removed from the active catalog.
# Creation requests referencing these are rejected with HTTP 410 per
# Requirements 1.9 and 2.1.
DEPRECATED_PLAN_IDS = {
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

    Used to decide whether to grant the 7-day Maigie Plus trial per
    Requirement 1.12. A user is treated as a first-time Plus subscriber
    when their stored ``Tier`` is ``FREE`` and they have no record of a
    paid plan in either provider.
    """
    if str(user.tier or "FREE") != "FREE":
        return False
    return not (user.stripeSubscriptionId or user.paystackSubscriptionCode)


def get_active_plan_catalog() -> PlanCatalogResponse:
    """Return the active product catalog.

    Per Requirement 1.10 the catalog contains exactly five entries:
    ``FREE``, ``PLUS_MONTHLY``, ``PLUS_YEARLY``, ``CIRCLE_PLAN_MONTHLY``,
    and ``PLUS_SEAT_ADD_ON_MONTHLY``. Deprecated ``STUDY_CIRCLE_*`` and
    ``SQUAD_*`` products are excluded (Requirements 1.6, 1.8, 17.9).

    Prices are sourced from ``Settings`` (cents, USD) so the catalog
    stays consistent with the marketing copy in Requirement 1.3.
    """
    cfg = get_settings()
    products = [
        PlanCatalogEntry(
            productId=PlanCatalogProductId.FREE,
            displayName="Free",
            scope=PlanCatalogScope.PERSONAL,
            priceCents=0,
            interval="NONE",
            description="Free personal tier with limited AI access.",
        ),
        PlanCatalogEntry(
            productId=PlanCatalogProductId.PLUS_MONTHLY,
            displayName="Maigie Plus (Monthly)",
            scope=PlanCatalogScope.PERSONAL,
            priceCents=cfg.PRICE_CENTS_PLUS_MONTHLY,
            interval="MONTH",
            trialDays=cfg.TRIAL_DAYS_MAIGIE_PLUS,
            description=(
                "Unlimited AI, advanced models, and larger uploads in your "
                "personal workspace."
            ),
        ),
        PlanCatalogEntry(
            productId=PlanCatalogProductId.PLUS_YEARLY,
            displayName="Maigie Plus (Yearly)",
            scope=PlanCatalogScope.PERSONAL,
            priceCents=cfg.PRICE_CENTS_PLUS_YEARLY,
            interval="YEAR",
            trialDays=cfg.TRIAL_DAYS_MAIGIE_PLUS,
            description=(
                "Unlimited AI, advanced models, and larger uploads, billed "
                "yearly."
            ),
        ),
        PlanCatalogEntry(
            productId=PlanCatalogProductId.CIRCLE_PLAN_MONTHLY,
            displayName="Circle Plan",
            scope=PlanCatalogScope.CIRCLE,
            priceCents=cfg.PRICE_CENTS_CIRCLE_PLAN_MONTHLY,
            interval="MONTH",
            trialDays=cfg.TRIAL_DAYS_CIRCLE_PLAN,
            description=(
                "Per-Circle plan with 4 included Plus seats and premium "
                "Circle features."
            ),
        ),
        PlanCatalogEntry(
            productId=PlanCatalogProductId.PLUS_SEAT_ADD_ON_MONTHLY,
            displayName="Plus Seat Add-on",
            scope=PlanCatalogScope.ADD_ON,
            priceCents=cfg.PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY,
            interval="MONTH",
            description=(
                "Adds one Plus seat to a Circle. Owners and admins can "
                "assign and reassign seats freely."
            ),
        ),
    ]
    return PlanCatalogResponse(products=products)


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

    Rejects deprecated plan ids (``study_circle_*`` / ``squad_*``) with
    ``DeprecatedPlanError`` per Requirements 1.9 and 2.1. Per Requirement
    1.12, the 7-day Maigie Plus trial is granted only on a user's first
    PLUS purchase; pass ``user`` to enforce this. When ``user`` is
    omitted (existing call sites that handle trial logic separately) the
    full configured trial length is returned.

    Args:
        plan_id: Active plan identifier.
        user: Optional purchasing user, used to suppress repeat trials.

    Returns:
        (price_id, trial_days)

    Raises:
        DeprecatedPlanError: If plan_id refers to a removed tier.
        ValueError: If plan_id is otherwise invalid.
    """
    assert_plan_id_is_active(plan_id)

    plus_trial = settings.TRIAL_DAYS_MAIGIE_PLUS
    if user is not None and not _is_first_plus_purchase(user):
        plus_trial = 0

    if plan_id in ("maigie_plus_monthly", "plus_monthly"):
        return settings.STRIPE_PRICE_ID_MONTHLY, plus_trial
    if plan_id in ("maigie_plus_yearly", "plus_yearly"):
        return settings.STRIPE_PRICE_ID_YEARLY, plus_trial
    if plan_id == "circle_plan_monthly":
        # The Circle Plan trial is owned by the Circle billing service.
        # Personal-checkout surface should not honor it; return 0 here.
        return settings.STRIPE_PRICE_ID_CIRCLE_PLAN_MONTHLY, 0
    if plan_id == "plus_seat_add_on_monthly":
        return settings.STRIPE_PRICE_ID_PLUS_SEAT_ADD_ON_MONTHLY, 0
    raise ValueError(f"Invalid plan_id: {plan_id}. " f"Must be one of: {', '.join(PLAN_IDS)}")


def _price_id_to_tier(price_id: str) -> str:
    """Map Stripe price ID to tier enum value.

    The active tiers are ``PREMIUM_MONTHLY`` / ``PREMIUM_YEARLY`` (the
    storage representation of the user-facing ``PLUS_*`` aliases).
    Deprecated ``STUDY_CIRCLE_*`` and ``SQUAD_*`` price IDs are still
    mapped here so historical billing records and webhook events for
    legacy subscriptions continue to resolve their source tier
    (Requirement 2.8 retains historical billing for ≥24 months); the
    active checkout surface rejects creation against them via
    ``assert_plan_id_is_active`` and ``_assert_price_id_is_active``.
    """
    if price_id == settings.STRIPE_PRICE_ID_MONTHLY:
        return "PREMIUM_MONTHLY"
    if price_id == settings.STRIPE_PRICE_ID_YEARLY:
        return "PREMIUM_YEARLY"
    if price_id == settings.STRIPE_PRICE_ID_STUDY_CIRCLE_MONTHLY:
        return "STUDY_CIRCLE_MONTHLY"
    if price_id == settings.STRIPE_PRICE_ID_STUDY_CIRCLE_YEARLY:
        return "STUDY_CIRCLE_YEARLY"
    if price_id == settings.STRIPE_PRICE_ID_SQUAD_MONTHLY:
        return "SQUAD_MONTHLY"
    if price_id == settings.STRIPE_PRICE_ID_SQUAD_YEARLY:
        return "SQUAD_YEARLY"
    return "FREE"


def _assert_price_id_is_active(price_id: str) -> None:
    """Reject creation requests against a deprecated Stripe price ID.

    Per Requirements 1.9 and 2.1 (and the Property 2 contract in
    design.md), any subscription creation that targets a
    ``STUDY_CIRCLE_*`` or ``SQUAD_*`` price must fail with HTTP 410.
    """
    if price_id and price_id == settings.STRIPE_PRICE_ID_STUDY_CIRCLE_MONTHLY:
        raise DeprecatedPlanError(
            code="STUDY_CIRCLE_PLAN_REMOVED",
            message="The Study Circle plan has been retired.",
        )
    if price_id and price_id == settings.STRIPE_PRICE_ID_STUDY_CIRCLE_YEARLY:
        raise DeprecatedPlanError(
            code="STUDY_CIRCLE_PLAN_REMOVED",
            message="The Study Circle plan has been retired.",
        )
    if price_id and price_id == settings.STRIPE_PRICE_ID_SQUAD_MONTHLY:
        raise DeprecatedPlanError(
            code="SQUAD_PLAN_REMOVED",
            message="The Squad plan has been retired.",
        )
    if price_id and price_id == settings.STRIPE_PRICE_ID_SQUAD_YEARLY:
        raise DeprecatedPlanError(
            code="SQUAD_PLAN_REMOVED",
            message="The Squad plan has been retired.",
        )


async def get_or_create_stripe_customer(user: User) -> str:
    """
    Get existing Stripe customer ID or create a new customer.

    Args:
        user: User model instance

    Returns:
        Stripe customer ID
    """
    if user.stripeCustomerId:
        return user.stripeCustomerId

    # Create new Stripe customer
    customer = stripe.Customer.create(
        email=user.email,
        name=user.name,
        metadata={"user_id": user.id},
    )

    # Update user with Stripe customer ID
    await db.user.update(
        where={"id": user.id},
        data={"stripeCustomerId": customer.id},
    )

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
    if not user.stripeSubscriptionId:
        raise ValueError("User does not have an active subscription")

    # Reject modification target tiers that have been retired
    # (Requirements 1.9, 2.1).
    _assert_price_id_is_active(new_price_id)

    # Retrieve current subscription
    subscription = stripe.Subscription.retrieve(
        user.stripeSubscriptionId, expand=["items.data.price"]
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
                user.stripeSubscriptionId,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="create_prorations",  # Charge prorated amount now
                billing_cycle_anchor="now",  # Must use "now" for interval changes
                metadata={"user_id": user.id, "upgrade": "true", "interval_change": "true"},
            )
        else:
            # Upgrade within same interval (e.g., free to monthly, or price change)
            # Charge now (prorated), billing cycle unchanged
            modified_subscription = stripe.Subscription.modify(
                user.stripeSubscriptionId,
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
                user.stripeSubscriptionId,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                proration_behavior="none",  # Don't charge until next billing date
                billing_cycle_anchor="now",  # Must use "now" for interval changes
                metadata={"user_id": user.id, "downgrade": "true", "interval_change": "true"},
            )
        else:
            # Downgrade within same interval
            # Charge at next billing date, changes take effect at period end
            modified_subscription = stripe.Subscription.modify(
                user.stripeSubscriptionId,
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
    if user.stripeSubscriptionId:
        logger.info(
            f"User {user.id} has subscription ID {user.stripeSubscriptionId} in database, "
            f"attempting to modify instead of creating new checkout"
        )
        # User has existing subscription - modify it instead
        try:
            result = await modify_existing_subscription(user, price_id)
            logger.info(
                f"Successfully modified subscription for user {user.id}: "
                f"upgrade={result['is_upgrade']}, subscription_id={result['subscription_id']}"
            )
            # Return a dict that looks like a checkout session for compatibility
            # The frontend will handle this differently
            return {
                "session_id": result["subscription_id"],
                "url": None,  # No redirect needed, subscription already modified
                "modified": True,
                "is_upgrade": result["is_upgrade"],
                "current_period_end": result["current_period_end"].isoformat(),
            }
        except ValueError as e:
            if "already subscribed to this plan" in str(e).lower():
                # User already has this plan — sync tier from Stripe and return success
                logger.info(
                    f"User {user.id} already subscribed to requested plan. "
                    "Syncing subscription data from Stripe."
                )
                updated_user = await update_user_subscription_from_stripe(user.stripeSubscriptionId)
                if updated_user:
                    user = updated_user
                return {
                    "session_id": user.stripeSubscriptionId,
                    "url": None,
                    "modified": False,
                    "is_upgrade": False,
                    "current_period_end": (
                        user.subscriptionCurrentPeriodEnd.isoformat()
                        if user.subscriptionCurrentPeriodEnd
                        else None
                    ),
                }
            # For other validation errors, raise as before
            logger.warning(f"Cannot modify subscription for user {user.id}: {e}")
            raise
        except stripe.error.StripeError as e:
            # Stripe-specific errors should be raised, not silently ignored
            logger.error(
                f"Stripe error modifying subscription for user {user.id}: {e}",
                exc_info=True,
            )
            raise ValueError(f"Failed to modify subscription: {str(e)}")
        except Exception as e:
            # Other unexpected errors - log and raise instead of silently falling through
            logger.error(
                f"Unexpected error modifying subscription for user {user.id}: {e}",
                exc_info=True,
            )
            raise ValueError(f"Failed to modify subscription: {str(e)}")
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
                updated_user = await update_user_subscription_from_stripe(
                    active_subscription.id, db
                )
                if not updated_user:
                    # Fallback to manual ID update if standard sync fails
                    await db.user.update(
                        where={"id": user.id},
                        data={"stripeSubscriptionId": active_subscription.id},
                    )
                    user.stripeSubscriptionId = active_subscription.id
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
                            "session_id": user.stripeSubscriptionId,
                            "url": None,
                            "modified": False,
                            "is_upgrade": False,
                            "current_period_end": (
                                user.subscriptionCurrentPeriodEnd.isoformat()
                                if user.subscriptionCurrentPeriodEnd
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


async def create_portal_session(user: User, return_url: str) -> dict:
    """
    Create a Stripe customer portal session for subscription management.

    Args:
        user: User model instance
        return_url: URL to redirect after portal session

    Returns:
        Portal session object with URL
    """
    if not user.stripeCustomerId:
        raise ValueError("User does not have a Stripe customer ID")

    session = stripe.billing_portal.Session.create(
        customer=user.stripeCustomerId,
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
    if not user.stripeSubscriptionId:
        raise ValueError("User does not have an active subscription")

    subscription = stripe.Subscription.modify(
        user.stripeSubscriptionId,
        cancel_at_period_end=True,
    )

    # Update user subscription status
    await db.user.update(
        where={"id": user.id},
        data={
            "stripeSubscriptionStatus": subscription.status,
        },
    )

    return {
        "status": subscription.status,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
    }


async def sync_subscription_from_checkout_session(
    session_id: str, user_id: str, db_client: Prisma | None = None
) -> User | None:
    """
    Sync user subscription from a Stripe checkout session (e.g. after free trial signup).
    Called when user returns from checkout with session_id before webhooks may have fired.

    Args:
        session_id: Stripe checkout session ID (cs_xxx)
        user_id: ID of the current user (must own this session)
        db_client: Optional Prisma client

    Returns:
        Updated User or None if session invalid/not found
    """
    if db_client is None:
        db_client = db
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
        updated = await update_user_subscription_from_stripe(sub_id, db_client)
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
    subscription_id: str, db_client: Prisma | None = None
) -> User | None:
    """
    Update user subscription data from Stripe subscription object.

    Args:
        subscription_id: Stripe subscription ID
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object or None if not found
    """
    if db_client is None:
        db_client = db

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
        user = await db_client.user.find_unique(where={"stripeCustomerId": customer_id})

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

        # Check if this is a new billing period (period start changed)
        # This happens when a new subscription starts or renews
        is_new_period = False
        if period_start_dt and user.subscriptionCurrentPeriodStart:
            # Period start changed, meaning new billing cycle
            if period_start_dt != user.subscriptionCurrentPeriodStart:
                is_new_period = True
        elif period_start_dt and not user.subscriptionCurrentPeriodStart:
            # First time setting period start
            is_new_period = True

        # Update user subscription data
        updated_user = await db_client.user.update(
            where={"id": user.id},
            data={
                "stripeSubscriptionId": sub_id,
                "stripeSubscriptionStatus": sub_status,
                "stripePriceId": price_id,
                "tier": tier,
                "paymentProvider": "stripe",
                "subscriptionCurrentPeriodStart": period_start_dt,
                "subscriptionCurrentPeriodEnd": period_end_dt,
            },
        )

        # Reset credits if this is a new billing period
        if is_new_period and period_start_dt and period_end_dt:
            try:
                updated_user = await reset_credits_for_period_start(
                    updated_user, period_start_dt, period_end_dt, db_client
                )
                logger.info(f"Reset credits for user {user.id} due to new subscription period")
            except Exception as e:
                logger.error(f"Failed to reset credits for user {user.id}: {e}")
                # Don't fail the subscription update if credit reset fails

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

            # Track referral subscription reward
            try:
                await track_referral_subscription(updated_user, db_client)
            except Exception as e:
                # Don't fail subscription update if referral tracking fails
                logger.error(f"Failed to track referral subscription: {e}")

        return updated_user

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error updating subscription: {e}")
        raise
    except Exception as e:
        logger.error(f"Error updating user subscription: {e}")
        raise


async def handle_subscription_webhook(
    event_type: str, subscription: dict, db_client: Prisma | None = None
) -> None:
    """
    Handle Stripe webhook events for subscriptions.

    Args:
        event_type: Stripe event type (e.g., 'customer.subscription.created')
        subscription: Stripe subscription object
        db_client: Optional Prisma client (defaults to global db)
    """
    if db_client is None:
        db_client = db

    subscription_id = subscription.get("id")
    if not subscription_id:
        logger.warning("Subscription ID not found in webhook data")
        return

    # Update user subscription data
    await update_user_subscription_from_stripe(subscription_id, db_client)

    # Handle specific event types
    if event_type == "customer.subscription.deleted":
        # Subscription was canceled - set user to FREE tier
        customer_id = subscription.get("customer")
        if customer_id:
            user = await db_client.user.find_unique(where={"stripeCustomerId": customer_id})
            if user:
                await db_client.user.update(
                    where={"id": user.id},
                    data={
                        "tier": "FREE",
                        "stripeSubscriptionStatus": "canceled",
                        "stripeSubscriptionId": None,
                        "stripePriceId": None,
                        "subscriptionCurrentPeriodStart": None,
                        "subscriptionCurrentPeriodEnd": None,
                    },
                )
                logger.info(f"Subscription canceled for user: {user.id}")
