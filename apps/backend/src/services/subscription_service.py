"""
Subscription service for Stripe integration.

This module handles all Stripe subscription operations including:
- Creating checkout sessions
- Managing subscriptions
- Handling webhook events

Copyright (C) 2025 Maigie
"""

import logging
from datetime import datetime

import stripe
from prisma import Prisma
from prisma.models import User

from ..config import Settings, get_settings
from ..core.database import db
from ..services.email import send_subscription_success_email

logger = logging.getLogger(__name__)

# Initialize Stripe
settings = get_settings()
stripe.api_key = settings.STRIPE_SECRET_KEY


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
        current_tier: Current tier (FREE, PREMIUM_MONTHLY, PREMIUM_YEARLY)
        new_price_id: New Stripe price ID

    Returns:
        True if upgrade, False if downgrade
    """
    # Define tier hierarchy: FREE < PREMIUM_MONTHLY < PREMIUM_YEARLY
    tier_order = {"FREE": 0, "PREMIUM_MONTHLY": 1, "PREMIUM_YEARLY": 2}

    current_order = tier_order.get(current_tier, 0)

    new_tier = "FREE"
    if new_price_id == settings.STRIPE_PRICE_ID_MONTHLY:
        new_tier = "PREMIUM_MONTHLY"
    elif new_price_id == settings.STRIPE_PRICE_ID_YEARLY:
        new_tier = "PREMIUM_YEARLY"

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
    user: User, price_id: str, success_url: str, cancel_url: str
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
            # If same plan or other validation error, raise it
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
            # List active subscriptions for this customer
            subscriptions = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
            if subscriptions.data and len(subscriptions.data) > 0:
                active_subscription = subscriptions.data[0]
                logger.info(
                    f"Found active Stripe subscription {active_subscription.id} for customer {customer_id}, "
                    f"but user {user.id} doesn't have it in database. Updating database and modifying subscription."
                )
                # Update user record with subscription ID
                await db.user.update(
                    where={"id": user.id},
                    data={"stripeSubscriptionId": active_subscription.id},
                )
                # Refresh user object
                user.stripeSubscriptionId = active_subscription.id
                # Now try to modify
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
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[
            {
                "price": price_id,
                "quantity": 1,
            }
        ],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user.id},
        subscription_data={
            "metadata": {"user_id": user.id},
        },
    )

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

        tier = "FREE"
        if price_id == settings.STRIPE_PRICE_ID_MONTHLY:
            tier = "PREMIUM_MONTHLY"
        elif price_id == settings.STRIPE_PRICE_ID_YEARLY:
            tier = "PREMIUM_YEARLY"

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

        # Update user subscription data
        updated_user = await db_client.user.update(
            where={"id": user.id},
            data={
                "stripeSubscriptionId": sub_id,
                "stripeSubscriptionStatus": sub_status,
                "stripePriceId": price_id,
                "tier": tier,
                "subscriptionCurrentPeriodStart": (
                    datetime.fromtimestamp(sub_period_start) if sub_period_start else None
                ),
                "subscriptionCurrentPeriodEnd": (
                    datetime.fromtimestamp(sub_period_end) if sub_period_end else None
                ),
            },
        )

        # Send email if upgraded from FREE to Premium
        # Convert enum to string for comparison just in case
        old_tier = str(user.tier) if user.tier else "FREE"
        new_tier = str(updated_user.tier)

        if old_tier == "FREE" and new_tier.startswith("PREMIUM"):
            try:
                # Run as background task or just await (it's async)
                await send_subscription_success_email(
                    email=updated_user.email,
                    name=updated_user.name or "User",
                    tier=new_tier,
                )
            except Exception as e:
                logger.error(f"Failed to send subscription success email: {e}")

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
