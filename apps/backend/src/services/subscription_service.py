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


async def create_checkout_session(
    user: User, price_id: str, success_url: str, cancel_url: str
) -> dict:
    """
    Create a Stripe checkout session for subscription.

    Args:
        user: User model instance
        price_id: Stripe price ID (monthly or yearly)
        success_url: URL to redirect after successful payment
        cancel_url: URL to redirect if user cancels

    Returns:
        Checkout session object
    """
    customer_id = await get_or_create_stripe_customer(user)

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
        subscription = stripe.Subscription.retrieve(
            subscription_id, expand=["items.data.price"]
        )
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
        # Handle both Stripe object and dict formats
        price_id = None
        if hasattr(subscription, "items"):
            # Stripe object - items might be a list or have a data attribute
            items = subscription.items
            if hasattr(items, "data") and items.data:
                price_id = items.data[0].price.id
            elif isinstance(items, list) and len(items) > 0:
                price_id = items[0].price.id
        elif isinstance(subscription, dict):
            # Dict format from webhook
            items = subscription.get("items", {})
            if isinstance(items, dict) and items.get("data"):
                price_id = items["data"][0]["price"]["id"]
            elif isinstance(items, list) and len(items) > 0:
                price_id = items[0].get("price", {}).get("id")

        tier = "FREE"
        if price_id == settings.STRIPE_PRICE_ID_MONTHLY:
            tier = "PREMIUM_MONTHLY"
        elif price_id == settings.STRIPE_PRICE_ID_YEARLY:
            tier = "PREMIUM_YEARLY"

        # Update user subscription data
        updated_user = await db_client.user.update(
            where={"id": user.id},
            data={
                "stripeSubscriptionId": subscription.id,
                "stripeSubscriptionStatus": subscription.status,
                "stripePriceId": price_id,
                "tier": tier,
                "subscriptionCurrentPeriodStart": (
                    datetime.fromtimestamp(subscription.current_period_start)
                    if subscription.current_period_start
                    else None
                ),
                "subscriptionCurrentPeriodEnd": (
                    datetime.fromtimestamp(subscription.current_period_end)
                    if subscription.current_period_end
                    else None
                ),
            },
        )

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
