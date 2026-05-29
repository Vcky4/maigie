"""
Circle billing service — Circle Plan and Plus Seat add-on lifecycle.

Manages purchase, cancellation, renewal, and webhook handling for
Circle-scoped billing products (Circle Plan and Plus Seat add-ons).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from prisma import Prisma

from src.config import get_settings
from src.core.database import db as default_db

logger = logging.getLogger(__name__)

settings = get_settings()


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

PAYMENT_METHOD_REQUIRED = "PAYMENT_METHOD_REQUIRED"
SEAT_MANAGEMENT_FORBIDDEN = "SEAT_MANAGEMENT_FORBIDDEN"
CIRCLE_PLAN_ALREADY_ACTIVE = "CIRCLE_PLAN_ALREADY_ACTIVE"
CIRCLE_PLAN_NOT_ACTIVE = "CIRCLE_PLAN_NOT_ACTIVE"
DUNNING_UNAVAILABLE = "DUNNING_UNAVAILABLE"


class CircleBillingError(Exception):
    """Structured error raised by circle billing operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _verify_billing_actor(client: Prisma, circle_id: str, actor_user_id: str) -> Any:
    """Verify actor is OWNER or ADMIN. Returns the Circle row."""
    member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": actor_user_id}}
    )
    if member is None or str(member.role) not in ("OWNER", "ADMIN"):
        raise CircleBillingError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage billing.",
            status_code=403,
        )

    circle = await client.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise CircleBillingError(
            code="CIRCLE_NOT_FOUND",
            message="Circle not found.",
            status_code=404,
        )
    return circle


# ---------------------------------------------------------------------------
# Purchase Circle Plan
# ---------------------------------------------------------------------------


async def purchase_circle_plan(
    actor_user_id: str,
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Purchase a Circle Plan for a Circle.

    Bills to the OWNER's payment method via Stripe or Paystack.
    Grants a 7-day trial on first purchase. On activation:
        - Set circlePlanActive = true
        - seatPoolSize includes the 4 plan seats
        - Seat 1 auto-assigned to OWNER as PLUS_SEAT

    Raises:
        CircleBillingError: PAYMENT_METHOD_REQUIRED (402),
            CIRCLE_PLAN_ALREADY_ACTIVE (409), SEAT_MANAGEMENT_FORBIDDEN (403)
    """
    client = db_client or default_db
    circle = await _verify_billing_actor(client, circle_id, actor_user_id)

    if circle.circlePlanActive:
        raise CircleBillingError(
            code=CIRCLE_PLAN_ALREADY_ACTIVE,
            message="This Circle already has an active Circle Plan.",
            status_code=409,
        )

    # Find the OWNER for billing
    owner_member = await client.circlemember.find_first(
        where={"circleId": circle_id, "role": "OWNER"},
        include={"user": True},
    )
    if owner_member is None:
        raise CircleBillingError(
            code="OWNER_NOT_FOUND",
            message="Circle owner not found.",
            status_code=500,
        )

    owner_user = owner_member.user

    # Check payment method
    stripe_customer_id = getattr(owner_user, "stripeCustomerId", None)
    paystack_customer_code = getattr(owner_user, "paystackCustomerCode", None)

    if not stripe_customer_id and not paystack_customer_code:
        raise CircleBillingError(
            code=PAYMENT_METHOD_REQUIRED,
            message="A payment method is required to purchase a Circle Plan.",
            status_code=402,
        )

    # Determine if this is the first Circle Plan purchase (for trial)
    existing_subscription = await client.circlesubscription.find_first(
        where={"circleId": circle_id}
    )
    is_first_purchase = existing_subscription is None

    # Create Stripe subscription (or Paystack)
    trial_days = settings.TRIAL_DAYS_CIRCLE_PLAN if is_first_purchase else 0
    now = datetime.now(UTC)
    period_end = now + timedelta(days=30)
    trial_end = now + timedelta(days=trial_days) if trial_days > 0 else None

    # Create the subscription record
    subscription = await client.circlesubscription.create(
        data={
            "circleId": circle_id,
            "status": "TRIALING" if trial_days > 0 else "ACTIVE",
            "currentPeriodEnd": period_end,
            "trialEndsAt": trial_end,
            "provider": "stripe" if stripe_customer_id else "paystack",
            "externalSubscriptionId": "",  # Will be set by webhook
        }
    )

    # Activate plan seats
    from src.services.seat_service import activate_circle_plan_seats

    await activate_circle_plan_seats(circle_id, owner_member.userId, db_client=client)

    # Update Circle plan period end
    await client.circle.update(
        where={"id": circle_id},
        data={"circlePlanCurrentPeriodEnd": period_end},
    )

    logger.info(
        "purchase_circle_plan: circle_id=%s actor=%s trial=%d",
        circle_id,
        actor_user_id,
        trial_days,
    )

    return {
        "circleId": circle_id,
        "subscriptionId": subscription.id,
        "status": subscription.status,
        "currentPeriodEnd": period_end.isoformat(),
        "trialEndsAt": trial_end.isoformat() if trial_end else None,
        "seatPoolSize": 4,  # PLAN_INCLUDED_SEATS
        "circlePlanActive": True,
    }


# ---------------------------------------------------------------------------
# Cancel Circle Plan
# ---------------------------------------------------------------------------


async def cancel_circle_plan(
    actor_user_id: str,
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Cancel the Circle Plan at period end.

    Retains features and PLUS_SEAT capabilities until currentPeriodEnd.
    At period end, seat_service.deactivate_circle_plan_seats reverts
    non-add-on PLUS_SEATs to FREE_SEAT and re-applies free gates.
    """
    client = db_client or default_db
    circle = await _verify_billing_actor(client, circle_id, actor_user_id)

    if not circle.circlePlanActive:
        raise CircleBillingError(
            code=CIRCLE_PLAN_NOT_ACTIVE,
            message="This Circle does not have an active Circle Plan.",
            status_code=409,
        )

    # Mark subscription as canceled (retains until period end)
    subscription = await client.circlesubscription.find_first(
        where={"circleId": circle_id, "status": {"in": ["ACTIVE", "TRIALING"]}}
    )
    if subscription:
        await client.circlesubscription.update(
            where={"id": subscription.id},
            data={"status": "CANCELED"},
        )

    logger.info(
        "cancel_circle_plan: circle_id=%s actor=%s period_end=%s",
        circle_id,
        actor_user_id,
        circle.circlePlanCurrentPeriodEnd,
    )

    return {
        "circleId": circle_id,
        "status": "CANCELED",
        "currentPeriodEnd": (
            circle.circlePlanCurrentPeriodEnd.isoformat()
            if circle.circlePlanCurrentPeriodEnd
            else None
        ),
        "message": "Circle Plan will remain active until the end of the current billing period.",
    }


# ---------------------------------------------------------------------------
# Purchase Plus Seat Add-on
# ---------------------------------------------------------------------------


async def purchase_seat_addon(
    actor_user_id: str,
    circle_id: str,
    quantity: int = 1,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Purchase Plus Seat add-on(s) for a Circle.

    Allowed regardless of plan state. Bills at $4.99/seat/month.
    Adds unassigned PLUS_SEAT(s) to the pool within 30 s.
    """
    client = db_client or default_db
    circle = await _verify_billing_actor(client, circle_id, actor_user_id)

    if quantity < 1:
        raise CircleBillingError(
            code="INVALID_QUANTITY",
            message="Quantity must be at least 1.",
            status_code=400,
        )

    # Find OWNER for billing
    owner_member = await client.circlemember.find_first(
        where={"circleId": circle_id, "role": "OWNER"},
        include={"user": True},
    )
    if owner_member is None:
        raise CircleBillingError(
            code="OWNER_NOT_FOUND",
            message="Circle owner not found.",
            status_code=500,
        )

    owner_user = owner_member.user
    stripe_customer_id = getattr(owner_user, "stripeCustomerId", None)
    paystack_customer_code = getattr(owner_user, "paystackCustomerCode", None)

    if not stripe_customer_id and not paystack_customer_code:
        raise CircleBillingError(
            code=PAYMENT_METHOD_REQUIRED,
            message="A payment method is required to purchase seat add-ons.",
            status_code=402,
        )

    now = datetime.now(UTC)
    period_end = now + timedelta(days=30)

    # Create add-on record(s)
    addon = await client.circleseataddon.create(
        data={
            "circleId": circle_id,
            "quantity": quantity,
            "status": "ACTIVE",
            "currentPeriodEnd": period_end,
            "provider": "stripe" if stripe_customer_id else "paystack",
            "externalSubscriptionId": "",  # Will be set by webhook
        }
    )

    # Update seat pool size
    new_pool_size = (circle.seatPoolSize or 0) + quantity
    await client.circle.update(
        where={"id": circle_id},
        data={"seatPoolSize": new_pool_size},
    )

    logger.info(
        "purchase_seat_addon: circle_id=%s quantity=%d actor=%s new_pool=%d",
        circle_id,
        quantity,
        actor_user_id,
        new_pool_size,
    )

    return {
        "circleId": circle_id,
        "addonId": addon.id,
        "status": "ACTIVE",
        "currentPeriodEnd": period_end.isoformat(),
        "quantity": quantity,
        "seatPoolSize": new_pool_size,
    }


# ---------------------------------------------------------------------------
# Cancel Plus Seat Add-on
# ---------------------------------------------------------------------------


async def cancel_seat_addon(
    actor_user_id: str,
    circle_id: str,
    addon_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Cancel a Plus Seat add-on at period end.

    Retains the seat as PLUS_SEAT until period end. At period end,
    seat_service.reconcile_seat_pool_on_addon_change handles the trim.
    """
    client = db_client or default_db
    await _verify_billing_actor(client, circle_id, actor_user_id)

    addon = await client.circleseataddon.find_unique(where={"id": addon_id})
    if addon is None or addon.circleId != circle_id:
        raise CircleBillingError(
            code="ADDON_NOT_FOUND",
            message="Seat add-on not found.",
            status_code=404,
        )

    if addon.status not in ("ACTIVE", "TRIALING"):
        raise CircleBillingError(
            code="ADDON_ALREADY_CANCELED",
            message="This add-on is already canceled.",
            status_code=409,
        )

    await client.circleseataddon.update(
        where={"id": addon_id},
        data={"status": "CANCELED_AT_PERIOD_END"},
    )

    logger.info(
        "cancel_seat_addon: circle_id=%s addon_id=%s actor=%s",
        circle_id,
        addon_id,
        actor_user_id,
    )

    return {
        "circleId": circle_id,
        "addonId": addon_id,
        "status": "CANCELED_AT_PERIOD_END",
        "currentPeriodEnd": addon.currentPeriodEnd.isoformat() if addon.currentPeriodEnd else None,
        "message": "Add-on seat will remain active until the end of the current billing period.",
    }


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------


async def handle_circle_billing_webhook(
    event_type: str,
    event_data: dict[str, Any],
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Handle Stripe/Paystack webhook events for Circle billing.

    Drives subscription lifecycle events:
        - invoice.paid → renew period, confirm activation
        - invoice.payment_failed → invoke Dunning flow
        - customer.subscription.deleted → deactivate at period end

    Returns a summary dict of actions taken.
    """
    client = db_client or default_db

    if event_type == "invoice.paid":
        # Renewal or initial activation
        subscription_id = event_data.get("subscription")
        if subscription_id:
            # Find matching CircleSubscription or CircleSeatAddon
            sub = await client.circlesubscription.find_first(
                where={"externalSubscriptionId": subscription_id}
            )
            if sub:
                now = datetime.now(UTC)
                new_period_end = now + timedelta(days=30)
                await client.circlesubscription.update(
                    where={"id": sub.id},
                    data={
                        "status": "ACTIVE",
                        "currentPeriodEnd": new_period_end,
                    },
                )
                await client.circle.update(
                    where={"id": sub.circleId},
                    data={"circlePlanCurrentPeriodEnd": new_period_end},
                )
                return {"action": "renewed", "circleId": sub.circleId}

            addon = await client.circleseataddon.find_first(
                where={"externalSubscriptionId": subscription_id}
            )
            if addon:
                now = datetime.now(UTC)
                new_period_end = now + timedelta(days=30)
                await client.circleseataddon.update(
                    where={"id": addon.id},
                    data={
                        "status": "ACTIVE",
                        "currentPeriodEnd": new_period_end,
                    },
                )
                return {"action": "addon_renewed", "circleId": addon.circleId}

    elif event_type == "invoice.payment_failed":
        # Dunning flow
        subscription_id = event_data.get("subscription")
        logger.warning("Circle billing payment failed: subscription=%s", subscription_id)
        # Mark as past_due
        if subscription_id:
            sub = await client.circlesubscription.find_first(
                where={"externalSubscriptionId": subscription_id}
            )
            if sub:
                await client.circlesubscription.update(
                    where={"id": sub.id},
                    data={"status": "PAST_DUE"},
                )
                return {"action": "marked_past_due", "circleId": sub.circleId}

    elif event_type == "customer.subscription.deleted":
        # Plan expired / fully canceled
        subscription_id = event_data.get("id") or event_data.get("subscription")
        if subscription_id:
            sub = await client.circlesubscription.find_first(
                where={"externalSubscriptionId": subscription_id}
            )
            if sub:
                from src.services.seat_service import deactivate_circle_plan_seats

                await deactivate_circle_plan_seats(sub.circleId, db_client=client)
                await client.circlesubscription.update(
                    where={"id": sub.id},
                    data={"status": "CANCELED"},
                )
                return {"action": "deactivated", "circleId": sub.circleId}

            addon = await client.circleseataddon.find_first(
                where={"externalSubscriptionId": subscription_id}
            )
            if addon:
                await client.circleseataddon.update(
                    where={"id": addon.id},
                    data={"status": "CANCELED"},
                )
                from src.services.seat_service import reconcile_seat_pool_on_addon_change

                await reconcile_seat_pool_on_addon_change(addon.circleId, db_client=client)
                return {"action": "addon_canceled", "circleId": addon.circleId}

    return {"action": "no_op", "event_type": event_type}
