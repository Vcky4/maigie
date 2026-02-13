"""
Paystack subscription service for Nigerian payments.

Handles subscription creation via Paystack plans, transaction verification,
and webhook processing.

Copyright (C) 2025 Maigie
"""

import logging
from datetime import datetime
from typing import Any

import httpx
from prisma import Prisma
from prisma.models import User

from ..config import get_settings
from ..core.database import db
from ..services.credit_service import reset_credits_for_period_start
from ..services.email import send_subscription_success_email
from ..services.referral_service import track_referral_subscription

logger = logging.getLogger(__name__)
PAYSTACK_BASE = "https://api.paystack.co"
PLAN_IDS = (
    "maigie_plus_monthly",
    "maigie_plus_yearly",
    "study_circle_monthly",
    "study_circle_yearly",
    "squad_monthly",
    "squad_yearly",
)


def _get_plan_code(plan_id: str) -> str:
    """Map plan_id to Paystack plan code."""
    settings = get_settings()
    mapping = {
        "maigie_plus_monthly": settings.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY,
        "maigie_plus_yearly": settings.PAYSTACK_PLAN_MAIGIE_PLUS_YEARLY,
        "study_circle_monthly": settings.PAYSTACK_PLAN_STUDY_CIRCLE_MONTHLY,
        "study_circle_yearly": settings.PAYSTACK_PLAN_STUDY_CIRCLE_YEARLY,
        "squad_monthly": settings.PAYSTACK_PLAN_SQUAD_MONTHLY,
        "squad_yearly": settings.PAYSTACK_PLAN_SQUAD_YEARLY,
    }
    code = mapping.get(plan_id)
    if not code:
        raise ValueError(f"Invalid plan_id: {plan_id}. Must be one of: {', '.join(PLAN_IDS)}")
    return code


def _plan_code_to_tier(plan_code: str) -> str:
    """Map Paystack plan code to tier."""
    settings = get_settings()
    if plan_code == settings.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY:
        return "PREMIUM_MONTHLY"
    if plan_code == settings.PAYSTACK_PLAN_MAIGIE_PLUS_YEARLY:
        return "PREMIUM_YEARLY"
    if plan_code == settings.PAYSTACK_PLAN_STUDY_CIRCLE_MONTHLY:
        return "STUDY_CIRCLE_MONTHLY"
    if plan_code == settings.PAYSTACK_PLAN_STUDY_CIRCLE_YEARLY:
        return "STUDY_CIRCLE_YEARLY"
    if plan_code == settings.PAYSTACK_PLAN_SQUAD_MONTHLY:
        return "SQUAD_MONTHLY"
    if plan_code == settings.PAYSTACK_PLAN_SQUAD_YEARLY:
        return "SQUAD_YEARLY"
    return "FREE"


async def initialize_paystack_subscription(
    user: User,
    plan_id: str,
    success_url: str,
    cancel_url: str,
    db_client: Prisma | None = None,
) -> dict:
    """
    Initialize a Paystack subscription transaction.

    Returns authorization_url for the user to complete payment.
    On success, Paystack creates the subscription and fires webhooks.
    """
    if db_client is None:
        db_client = db
    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValueError("Paystack is not configured (PAYSTACK_SECRET_KEY missing)")

    plan_code = _get_plan_code(plan_id)

    # Fetch plan amount from Paystack; transaction/initialize requires amount even when using plan.
    async with httpx.AsyncClient() as client:
        plan_resp = await client.get(
            f"{PAYSTACK_BASE}/plan/{plan_code}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        )
    plan_data = plan_resp.json()
    if not plan_data.get("status"):
        msg = plan_data.get("message", "Failed to fetch plan")
        logger.error(f"Paystack plan fetch failed for {plan_code}: {msg}")
        raise ValueError(f"Invalid plan: {msg}")
    plan_amount = plan_data.get("data", {}).get("amount")
    if plan_amount is None:
        raise ValueError(f"Plan {plan_code} has no amount")

    payload = {
        "email": user.email,
        "amount": str(int(plan_amount)),
        "plan": plan_code,
        "callback_url": success_url,
        "metadata": {"user_id": user.id, "plan_id": plan_id},
    }
    if user.name:
        payload["metadata"]["name"] = user.name

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
        )
    data = resp.json()
    if not data.get("status"):
        msg = data.get("message", "Paystack initialization failed")
        logger.error(f"Paystack init failed for user {user.id}: {msg}")
        raise ValueError(msg)

    result = data.get("data", {})
    return {
        "authorization_url": result.get("authorization_url"),
        "access_code": result.get("access_code"),
        "reference": result.get("reference"),
    }


async def verify_paystack_transaction(
    reference: str, user_id: str, db_client: Prisma | None = None
) -> User | None:
    """
    Verify a Paystack transaction and sync subscription to user.

    Call this when user returns from Paystack with ?reference=xxx.
    """
    if db_client is None:
        db_client = db
    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        )
    result = resp.json()
    if not result.get("status"):
        logger.warning(f"Paystack verify failed for ref {reference}: {result.get('message')}")
        return None

    tx = result.get("data", {})
    if tx.get("status") != "success":
        return None

    metadata = tx.get("metadata", {}) or {}
    plan_id = metadata.get("plan_id")
    plan = tx.get("plan") or {}
    plan_code = plan.get("plan_code") if isinstance(plan, dict) else None

    if plan_id:
        tier = _plan_code_to_tier(_get_plan_code(plan_id))
    elif plan_code:
        tier = _plan_code_to_tier(str(plan_code))
    else:
        logger.warning("No plan in Paystack transaction")
        return None

    customer = tx.get("customer") or {}
    customer_code = customer.get("customer_code") if isinstance(customer, dict) else None

    authorization = tx.get("authorization") or {}
    sub_code = tx.get("subscription_code")
    if not sub_code and isinstance(authorization, dict):
        sub_code = authorization.get("subscription_code")

    user = await db_client.user.find_unique(where={"id": user_id})
    if not user:
        return None

    # Determine period from plan
    plan_obj = tx.get("plan") or {}
    interval = plan_obj.get("interval", "monthly")
    from datetime import timedelta

    now = datetime.utcnow()
    if interval == "annually" or interval == "yearly":
        period_end = now + timedelta(days=365)
    else:
        period_end = now + timedelta(days=30)

    update_data = {
        "tier": tier,
        "paymentProvider": "paystack",
        "paystackSubscriptionCode": sub_code,
        "paystackCustomerCode": customer_code or user.paystackCustomerCode,
        "subscriptionCurrentPeriodStart": now,
        "subscriptionCurrentPeriodEnd": period_end,
        "stripeSubscriptionStatus": "active",
    }
    updated = await db_client.user.update(
        where={"id": user_id},
        data={k: v for k, v in update_data.items() if v is not None},
    )

    try:
        updated = await reset_credits_for_period_start(updated, now, period_end, db_client)
    except Exception as e:
        logger.error(f"Failed to reset credits for user {user_id}: {e}")

    old_tier = str(user.tier) if user.tier else "FREE"
    if old_tier == "FREE" and tier in (
        "PREMIUM_MONTHLY",
        "PREMIUM_YEARLY",
        "STUDY_CIRCLE_MONTHLY",
        "STUDY_CIRCLE_YEARLY",
        "SQUAD_MONTHLY",
        "SQUAD_YEARLY",
    ):
        try:
            await send_subscription_success_email(
                email=updated.email,
                name=updated.name or "User",
                tier=tier,
            )
            await track_referral_subscription(updated, db_client)
        except Exception as e:
            logger.error(f"Failed to send subscription email: {e}")

    return updated


async def handle_paystack_webhook(
    event: str, payload: dict, db_client: Prisma | None = None
) -> None:
    """
    Handle Paystack webhook events for subscriptions.

    Events: subscription.create, subscription.disable, charge.success
    """
    if db_client is None:
        db_client = db

    if event == "subscription.create":
        await _handle_subscription_create(payload, db_client)
    elif event == "subscription.disable":
        await _handle_subscription_disable(payload, db_client)
    elif event == "charge.success":
        await _handle_charge_success(payload, db_client)


async def _handle_subscription_create(payload: dict, db_client: Prisma) -> None:
    data = payload.get("data", {})
    customer = data.get("customer", {})
    customer_code = customer.get("customer_code") if isinstance(customer, dict) else customer
    email = customer.get("email") if isinstance(customer, dict) else None
    if not email and isinstance(customer, dict):
        email = data.get("email")

    if not email:
        user = await db_client.user.find_unique(where={"paystackCustomerCode": str(customer_code)})
    else:
        user = await db_client.user.find_first(where={"email": email})

    if not user:
        logger.warning(f"Paystack subscription.create: user not found for {email or customer_code}")
        return

    plan = data.get("plan", {})
    plan_code = plan.get("plan_code") if isinstance(plan, dict) else plan
    tier = _plan_code_to_tier(str(plan_code)) if plan_code else "FREE"

    sub_code = data.get("subscription_code")

    next_payment = data.get("next_payment_date")
    period_end = None
    if next_payment:
        try:
            # Paystack returns ISO format e.g. 2016-03-27T07:00:00.000Z
            s = str(next_payment).replace("Z", "+00:00")
            period_end = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            pass
    if not period_end:
        from datetime import timedelta

        period_end = datetime.utcnow() + timedelta(days=30)

    await db_client.user.update(
        where={"id": user.id},
        data={
            "tier": tier,
            "paymentProvider": "paystack",
            "paystackSubscriptionCode": sub_code,
            "paystackCustomerCode": customer_code or user.paystackCustomerCode,
            "subscriptionCurrentPeriodStart": datetime.utcnow(),
            "subscriptionCurrentPeriodEnd": period_end,
            "stripeSubscriptionStatus": "active",
        },
    )
    logger.info(f"Paystack subscription created for user {user.id}, tier={tier}")


async def _handle_subscription_disable(payload: dict, db_client: Prisma) -> None:
    data = payload.get("data", {})
    sub_code = data.get("subscription_code")
    if not sub_code:
        return
    user = await db_client.user.find_unique(where={"paystackSubscriptionCode": sub_code})
    if not user:
        return
    await db_client.user.update(
        where={"id": user.id},
        data={
            "tier": "FREE",
            "paystackSubscriptionCode": None,
            "stripeSubscriptionStatus": "cancelled",
            "subscriptionCurrentPeriodStart": None,
            "subscriptionCurrentPeriodEnd": None,
        },
    )
    logger.info(f"Paystack subscription disabled for user {user.id}")


async def _handle_charge_success(payload: dict, db_client: Prisma) -> None:
    """On successful charge (e.g. subscription renewal), sync subscription."""
    data = payload.get("data", {})
    metadata = data.get("metadata", {}) or {}
    user_id = metadata.get("user_id")
    reference = data.get("reference")
    if reference and user_id:
        await verify_paystack_transaction(str(reference), str(user_id), db_client)
