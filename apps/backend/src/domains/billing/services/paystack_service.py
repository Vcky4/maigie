"""
Paystack subscription service for Nigerian payments.

Handles subscription creation via Paystack plans, transaction verification,
and webhook processing.

Copyright (C) 2025 Maigie
"""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import get_settings
from src.domains.billing.repository import billing_repo
from src.domains.identity.db_models import User
from src.shared.exceptions import DeprecatedPlanError
from src.shared.infrastructure.email import send_subscription_success_email

logger = logging.getLogger(__name__)

# Ported to SQLAlchemy in Phase 2b. This module previously held
# `db = PrismaClientRemoved("billing.services.paystack_service")` and called
# `db_client.user.find_unique/update`, so **the NGN rail did not work at all** — and NGN is the
# launch market's rail, which is why the plan promoted this from a paragraph about deliberate
# absences to a phase of its own.
#
# Three things changed shape in the port, beyond the mechanical session work:
#
# **`db_client` is gone from every signature.** It was a Prisma client threaded through nine
# functions so that a caller could pass a transaction. Nothing ever passed one — every call site
# used the module-level default — and `billing_repo` owns its own sessions, so the parameter was a
# place for a future bug to hide rather than a capability. Removing it is what makes the reads
# below one line each.
#
# **Attributes are snake_case.** The Prisma model exposed `user.paystackSubscriptionCode`; the
# SQLAlchemy model maps that column to `user.paystack_subscription_code`. `tests/
# test_orm_attribute_names.py` guards the class of mistake this invites, and the essay at the top of
# `shared/schemas.py` explains why the columns stayed camelCase while the attributes did not.
#
# **`track_referral_subscription` is not ported, it is deleted.** Phase 3 removes the function,
# `referral_rewards_service` holds its own Prisma sentinel so the call could never have worked, and
# Decision O replaces referral rewards with a points ledger in which a subscription grants nothing.
# Writing SQLAlchemy for behaviour the plan has already withdrawn would have been the only reason to
# keep it.

PAYSTACK_BASE = "https://api.paystack.co"

# The plan id sets are imported from the Stripe service rather than restated here.
#
# They used to be a second copy, and a second copy of a product catalogue is how two
# surfaces come to sell different things: withdrawing yearly Plus from the Stripe checkout
# would have left the Paystack checkout still selling it, and the learner who found the
# difference would have been a Nigerian learner buying a product nobody else could.
#
# `PLAN_IDS` is only read to compose an error message here; `DEPRECATED_PLAN_IDS` decides
# what is refused. Both belong to the catalogue, and the catalogue has one owner.
from ..services.stripe_service import DEPRECATED_PLAN_IDS, PLAN_IDS  # noqa: E402


def _assert_plan_id_is_active(plan_id: str) -> None:
    """Reject creation against retired plan ids.

    Implements Requirements 1.9 and 2.1 for the Paystack surface.
    """
    if plan_id in DEPRECATED_PLAN_IDS:
        code, message = DEPRECATED_PLAN_IDS[plan_id]
        raise DeprecatedPlanError(code=code, message=message)


def _get_plan_code(plan_id: str) -> str:
    """Map plan_id to Paystack plan code.

    Rejects deprecated tiers with ``DeprecatedPlanError`` (HTTP 410) per
    Requirements 1.9 and 2.1.
    """
    _assert_plan_id_is_active(plan_id)
    settings = get_settings()
    # No yearly entry. `PAYSTACK_PLAN_MAIGIE_PLUS_YEARLY` still exists in config so a
    # renewal charge for a grandfathered yearly subscriber can be identified by
    # `_plan_code_to_tier`, but nothing may start a new one — `_assert_plan_id_is_active`
    # has already refused the id by the time this mapping is read, and keeping a row for it
    # here would only suggest otherwise to the next reader.
    mapping = {
        "maigie_plus_monthly": settings.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY,
        "plus_monthly": settings.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY,
        "circle_plan_monthly": settings.PAYSTACK_PLAN_CIRCLE_PLAN_MONTHLY,
        "plus_seat_add_on_monthly": settings.PAYSTACK_PLAN_PLUS_SEAT_ADD_ON_MONTHLY,
    }
    code = mapping.get(plan_id)
    if not code:
        raise ValueError(f"Invalid plan_id: {plan_id}. Must be one of: {', '.join(PLAN_IDS)}")
    return code


def _plan_code_to_tier(plan_code: str) -> str:
    """Map a Paystack plan code to a tier.

    **Only tiers the resolver honours**, for the reason spelled out on
    ``stripe_service._price_id_to_tier``: `entitlement_service.PLUS_TIERS` grants Plus for
    `PREMIUM_MONTHLY` alone, so producing any other paid string here would write a tier that bills a
    learner and entitles them to nothing.

    This previously mapped the yearly plan code and four retired Study Circle / Squad codes, to keep
    legacy webhooks resolving their source tier. Phase 2b removed them on a measurement —
    `scripts/count_legacy_commercial_state.py` found zero users on those tiers and zero Paystack
    subscription codes in the database — and because all five products are withdrawn, so no renewal
    for one can arrive.
    """
    settings = get_settings()
    if plan_code == settings.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY:
        return "PREMIUM_MONTHLY"
    return "FREE"


# Tier hierarchy for upgrade/downgrade comparison.
# Deprecated tiers retain a slot only for legacy comparison logic that
# reads existing user state; new creations cannot target them.
_TIER_ORDER = {
    "FREE": 0,
    "PREMIUM_MONTHLY": 1,
    "PREMIUM_YEARLY": 2,
    "STUDY_CIRCLE_MONTHLY": 3,
    "STUDY_CIRCLE_YEARLY": 4,
    "SQUAD_MONTHLY": 5,
    "SQUAD_YEARLY": 6,
}


def _plan_amount_kobo(plan_id: str) -> int:
    """The NGN price of a plan, in kobo, from config.

    Paystack takes minor units, so ₦2 400 is `240_000`. Prices are set for Nigeria rather than
    converted from USD — §6.8 of the plan has the argument, and the short version is that FX parity
    would put Maigie Plus above Netflix Standard for a Nigerian student.

    Falls back to the monthly price for a plan this does not know, which is only reachable for the
    two Circle-scoped products; they carry no NGN price of their own yet, and for a subscription the
    plan's amount overrides this field anyway.
    """
    settings = get_settings()
    amounts = {
        "maigie_plus_monthly": settings.PRICE_NGN_PLUS_MONTHLY,
        "plus_monthly": settings.PRICE_NGN_PLUS_MONTHLY,
        "plus_pass_5h": settings.PRICE_NGN_PLUS_PASS_5H,
        "plus_pass_7d": settings.PRICE_NGN_PLUS_PASS_7D,
    }
    return amounts.get(plan_id, settings.PRICE_NGN_PLUS_MONTHLY)


def _is_upgrade(current_tier: str, new_plan_id: str) -> bool:
    """Determine if changing from current tier to new plan is an upgrade."""
    current_order = _TIER_ORDER.get(current_tier, 0)
    new_tier = _plan_code_to_tier(_get_plan_code(new_plan_id))
    new_order = _TIER_ORDER.get(new_tier, 0)
    return new_order > current_order


async def disable_paystack_subscription(
    subscription_code: str,
    email_token: str,
) -> bool:
    """
    Disable (cancel) a Paystack subscription.

    Args:
        subscription_code: Paystack subscription code
        email_token: Paystack email token for the subscription

    Returns:
        True if successfully disabled
    """
    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValueError("Paystack is not configured")

    payload = {
        "code": subscription_code,
        "token": email_token,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{PAYSTACK_BASE}/subscription/disable",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
        )
    data = resp.json()
    if not data.get("status"):
        msg = data.get("message", "Failed to disable Paystack subscription")
        logger.error(f"Paystack disable failed: {msg}")
        raise ValueError(msg)

    logger.info(f"Paystack subscription {subscription_code} disabled")
    return True


#: The columns written when a Paystack subscription ends, in one place.
#:
#: `cancel_paystack_subscription` and `_handle_subscription_disable` are the same state change
#: reached two ways — the learner asking, and Paystack telling us. They held two copies of this
#: dict, already identical, which is the point at which one of them starts drifting.
_CANCELLED_SUBSCRIPTION_STATE: dict[str, Any] = {
    "tier": "FREE",
    "paystackSubscriptionCode": None,
    "stripeSubscriptionStatus": "cancelled",
    "subscriptionCurrentPeriodStart": None,
    "subscriptionCurrentPeriodEnd": None,
}


async def cancel_paystack_subscription(user: User) -> dict:
    """
    Cancel a Paystack subscription at the end of the period.

    Since Paystack doesn't have an automated 'cancel at period end' like Stripe
    internally for the API we use, we mark it as cancelled and disable it.
    """
    if not user.paystack_subscription_code:
        raise ValueError("User does not have an active Paystack subscription")

    subscription_code = user.paystack_subscription_code

    # Fetch email token for disable
    logger.info(f"Fetching Paystack email token for sub {subscription_code}")
    email_token = await _get_paystack_subscription_email_token(subscription_code)

    if not email_token:
        logger.warning(
            f"Could not retrieve cancellation token from Paystack for user {user.id} "
            f"(sub: {subscription_code}). Proceeding with local-only cancellation."
        )
    else:
        # Disable the subscription on Paystack
        try:
            await disable_paystack_subscription(subscription_code, email_token)
        except Exception as e:
            logger.error(f"Failed to disable Paystack subscription {subscription_code}: {e}")
            # We still proceed with local cleanup to avoid blocking the user

    # Note: In a real production app, we might want to keep the tier until period_end.
    # But Paystack 'disable' is immediate. For Maigie, we'll sync the DB to FREE.
    # If we want to allow access until period_end, we should store a 'is_cancelling' flag instead.
    # For now, we follow the existing disable logic which sets to FREE.

    # Read the period end *before* the update clears it. The returned value tells the learner what
    # they are giving up, and reading it afterwards would always have reported `None`.
    period_end = user.subscription_current_period_end

    await billing_repo.update_subscription(user.id, dict(_CANCELLED_SUBSCRIPTION_STATE))

    return {
        "status": "cancelled",
        "cancel_at_period_end": False,  # Paystack disable is immediate
        "current_period_end": period_end or datetime.now(UTC),
    }


async def _get_paystack_subscription_email_token(subscription_code: str) -> str | None:
    """Fetch the email_token for a Paystack subscription (needed for disable)."""
    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PAYSTACK_BASE}/subscription/{subscription_code}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        )
    data = resp.json()
    if data.get("status") and data.get("data"):
        return data["data"].get("email_token")
    return None


async def initialize_paystack_subscription(
    user: User,
    plan_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """
    Initialize a Paystack subscription transaction.

    If user already has a Paystack subscription, it will be disabled first
    (cancel-and-resubscribe approach for upgrade/downgrade).

    Returns authorization_url for the user to complete payment.
    On success, Paystack creates the subscription and fires webhooks.
    """
    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValueError("Paystack is not configured (PAYSTACK_SECRET_KEY missing)")

    plan_code = _get_plan_code(plan_id)
    logger.info(f"Paystack init: plan_id={plan_id}, plan_code={plan_code}")

    # Check if user already has an active Paystack subscription
    is_upgrade = None
    if user.paystack_subscription_code:
        existing_code = user.paystack_subscription_code
        current_tier = str(user.tier) if user.tier else "FREE"
        # Check if trying to subscribe to the same plan.
        # Map each active plan family to the storage tier values it covers.
        #
        # `PREMIUM_YEARLY` is gone from both families: yearly Plus is withdrawn, and Phase 2b
        # narrowed the writers so no code path can produce that tier any more. Leaving it here
        # would have meant a monthly subscriber being told they are "already subscribed to this
        # plan" on the strength of a tier value nothing can hold.
        plan_family_to_tiers = {
            "maigie_plus": ["PREMIUM_MONTHLY"],
            "plus": ["PREMIUM_MONTHLY"],
            # Circle Plan and the Plus Seat add-on are Circle-scoped products
            # not surfaced as user-level tiers. We do not block re-purchase
            # at this surface (Requirement 11.2 allows multiple add-ons), so
            # leave their family unmapped — same-plan detection only applies
            # to personal subscriptions.
        }
        plan_family = plan_id.rsplit("_", 1)[0]  # e.g. "plus" from "plus_monthly"
        if current_tier in plan_family_to_tiers.get(plan_family, []):
            raise ValueError("User is already subscribed to this plan")

        is_upgrade = _is_upgrade(current_tier, plan_id)
        logger.info(
            f"User {user.id} has existing Paystack subscription {existing_code}, "
            f"{'upgrading' if is_upgrade else 'downgrading'} to {plan_id}"
        )

        # Disable the existing subscription
        try:
            email_token = await _get_paystack_subscription_email_token(existing_code)
            if email_token:
                await disable_paystack_subscription(existing_code, email_token)
            else:
                logger.warning(
                    f"Could not get email_token for subscription {existing_code}, "
                    "proceeding with new subscription anyway"
                )
        except Exception as e:
            logger.error(f"Failed to disable existing Paystack subscription: {e}")
            # Continue with new subscription even if disable fails

    # Paystack requires `amount` even when a plan is supplied, and the plan's own amount wins — so
    # for a subscription this field has never mattered and the old `"10000"` (₦100) placeholder was
    # never charged to anyone.
    #
    # It is sent as the real price anyway, from `config`, because the field stops being inert the
    # moment Phase 5 adds **one-time pass charges**: those carry no plan, so nothing overrides the
    # amount and ₦100 *is* the price. Setting it correctly now means the pass work adds a call site
    # rather than discovering a placeholder in production. See §6.8 for the NGN ladder and why all
    # three prices sit under Paystack's ₦2 500 flat-fee threshold.
    metadata: dict[str, Any] = {"user_id": user.id, "plan_id": plan_id}
    if is_upgrade is not None:
        metadata["is_upgrade"] = is_upgrade
    payload = {
        "email": user.email,
        "amount": str(_plan_amount_kobo(plan_id)),
        "plan": plan_code,
        "callback_url": success_url,
        "metadata": metadata,
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
        "is_modification": is_upgrade is not None,
        "is_upgrade": is_upgrade,
    }


async def verify_paystack_transaction(reference: str, user_id: str) -> User | None:
    """
    Verify a Paystack transaction and sync subscription to user.

    Call this when user returns from Paystack with ?reference=xxx.
    """
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

    user = await billing_repo.get_user_billing(user_id)
    if not user:
        return None

    # Determine period from plan
    plan_obj = tx.get("plan") or {}
    interval = plan_obj.get("interval", "monthly")
    from datetime import timedelta

    now = datetime.now(UTC)
    if interval == "annually" or interval == "yearly":
        period_end = now + timedelta(days=365)
    else:
        period_end = now + timedelta(days=30)

    update_data = {
        "tier": tier,
        "paymentProvider": "paystack",
        "paystackSubscriptionCode": sub_code,
        "paystackCustomerCode": customer_code or user.paystack_customer_code,
        "subscriptionCurrentPeriodStart": now,
        "subscriptionCurrentPeriodEnd": period_end,
        "stripeSubscriptionStatus": "active",
    }
    updated = await billing_repo.update_subscription(
        user_id,
        {k: v for k, v in update_data.items() if v is not None},
    )

    # `reset_credits_for_period_start` was called here to zero the month's credits and align the
    # credit period with the new billing period. Phase 3 deleted it: usage is a rolling 5-hour
    # window, so nothing in the meter is keyed to a billing period and a subscription event has no
    # counter to reset. What the subscription changes is the *allowance*, and that is read live from
    # `entitlement_service.resolve` rather than copied onto the user at payment time — which is what
    # kept the two from drifting apart when a payment succeeded and this write did not.

    # The welcome email fires on the FREE → paid transition only, so a renewal does not resend it.
    # `PREMIUM_MONTHLY` is the whole list now: Phase 2b narrowed `_plan_code_to_tier` so it is the
    # only paid tier this function can be handed, and enumerating five values it can no longer
    # produce would tell the next reader they are still reachable.
    #
    # `track_referral_subscription` was called here and is deleted rather than ported — Phase 3
    # removes the function and Decision O replaces referral rewards with a points ledger in which a
    # subscription grants nothing. It also could not have run: `referral_rewards_service` holds its
    # own Prisma sentinel. Note it sat *inside* this `try`, so its failure was swallowed by a
    # handler whose message says the email failed.
    old_tier = str(user.tier) if user.tier else "FREE"
    if old_tier == "FREE" and tier == "PREMIUM_MONTHLY":
        try:
            await send_subscription_success_email(
                email=updated.email,
                name=updated.name or "User",
                tier=tier,
            )
        except Exception as e:
            logger.error(f"Failed to send subscription email: {e}")

    return updated


async def handle_paystack_webhook(event: str, payload: dict) -> None:
    """
    Handle Paystack webhook events for subscriptions.

    Events: subscription.create, subscription.disable, charge.success

    **Unrecognised events raise.** `webhooks.py` answers `500` on an exception so Paystack retries,
    and silently returning `None` for an event we do not handle is indistinguishable from having
    processed it. Paystack sends events beyond these three, so the caller filters — this function
    refusing is the backstop for a filter that drifts, not the filter itself.
    """
    if event == "subscription.create":
        await _handle_subscription_create(payload)
    elif event == "subscription.disable":
        await _handle_subscription_disable(payload)
    elif event == "charge.success":
        await _handle_charge_success(payload)
    else:
        raise ValueError(f"Unhandled Paystack webhook event: {event}")


async def _handle_subscription_create(payload: dict) -> None:
    data = payload.get("data", {})
    customer = data.get("customer", {})
    customer_code = customer.get("customer_code") if isinstance(customer, dict) else customer
    email = customer.get("email") if isinstance(customer, dict) else None
    if not email and isinstance(customer, dict):
        email = data.get("email")

    if email:
        user = await billing_repo.find_user_by_email(str(email))
    elif customer_code:
        user = await billing_repo.find_user_by_paystack_customer(str(customer_code))
    else:
        user = None

    if not user:
        # Raise rather than return: `webhooks.py` turns this into a `500` so Paystack retries, and a
        # subscription we cannot attribute is money taken with nothing granted. Returning quietly
        # logged a warning and answered `200`, which told Paystack the event was handled.
        raise ValueError(f"Paystack subscription.create: no user for {email or customer_code}")

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
    now = datetime.now(UTC)
    if not period_end:
        from datetime import timedelta

        period_end = now + timedelta(days=30)

    await billing_repo.update_subscription(
        user.id,
        {
            "tier": tier,
            "paymentProvider": "paystack",
            "paystackSubscriptionCode": sub_code,
            "paystackCustomerCode": customer_code or user.paystack_customer_code,
            "subscriptionCurrentPeriodStart": now,
            "subscriptionCurrentPeriodEnd": period_end,
            "stripeSubscriptionStatus": "active",
        },
    )
    logger.info(f"Paystack subscription created for user {user.id}, tier={tier}")


async def _handle_subscription_disable(payload: dict) -> None:
    data = payload.get("data", {})
    sub_code = data.get("subscription_code")
    if not sub_code:
        raise ValueError("Paystack subscription.disable carried no subscription_code")
    user = await billing_repo.find_user_by_paystack_subscription(str(sub_code))
    if not user:
        # A disable for a subscription code we do not hold. Unlike `subscription.create`, this is
        # benign and idempotent — most often our own `cancel_paystack_subscription` having already
        # cleared the code before Paystack's event arrived. Nothing is owed, so do not force a retry.
        logger.info(f"Paystack subscription.disable: no user holds {sub_code}; already cleared")
        return

    await billing_repo.update_subscription(user.id, dict(_CANCELLED_SUBSCRIPTION_STATE))
    logger.info(f"Paystack subscription disabled for user {user.id}")


async def _handle_charge_success(payload: dict) -> None:
    """A successful charge: a subscription's first payment or its renewal.

    The credit-pack branch is deleted rather than ported. It looked up a pending
    `CreditPurchaseTransaction` by provider reference and called
    `credit_purchase_service.fulfill_purchase`; credit packs are withdrawn (§6.1), Decision H drops
    the table, and Phase 2b's count found zero completed purchases, so there was nothing for it to
    find. `fulfill_purchase` goes with it as the table's last reader.

    Its error handling is worth recording, because it is the shape this port is removing throughout:
    the whole branch was wrapped in `except Exception: log; return`, with the comment "webhook should
    still return 200". So a credit-pack purchase that failed to fulfil was money taken, nothing
    granted, and an acknowledgement to Paystack that we had handled it.

    One-time **pass** charges will arrive here in Phase 5 and are a different shape: they carry no
    plan, and they resolve against `PlusPurchase` by `providerReference` rather than against a credit
    table. That is a new branch to write, not this one to keep.
    """
    data = payload.get("data", {})
    metadata = data.get("metadata", {}) or {}
    user_id = metadata.get("user_id")
    reference = data.get("reference")

    if not (reference and user_id):
        # Without both we cannot attribute the charge, and a charge we cannot attribute is money
        # taken with nothing granted — so this raises rather than returning quietly.
        raise ValueError(
            f"Paystack charge.success is unattributable "
            f"(reference={reference!r}, metadata.user_id={user_id!r})"
        )

    updated = await verify_paystack_transaction(str(reference), str(user_id))
    if updated is None:
        raise ValueError(f"Paystack charge.success for reference={reference} could not be verified")
