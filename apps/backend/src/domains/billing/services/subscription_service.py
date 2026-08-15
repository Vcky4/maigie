"""
Subscription lifecycle management.

Handles checkout sessions, plan upgrades/downgrades, cancellation,
portal sessions, and sync from payment providers.
"""

import logging
from typing import Any

from src.domains.identity.db_models import User
from src.shared.events import emit

from ..repository import billing_repo

logger = logging.getLogger(__name__)


async def get_plan_catalog() -> dict[str, Any]:
    """Return the active product catalog (public, no auth required)."""
    from src.domains.billing.services.stripe_service import get_active_plan_catalog

    return get_active_plan_catalog()


async def create_checkout_session(
    *,
    user: User,
    plan_id: str,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    """Create a Stripe checkout session for a subscription."""
    from src.domains.billing.services.stripe_service import (
        create_checkout_session as _stripe_checkout,
    )
    from src.domains.billing.services.stripe_service import (
        get_price_id_and_trial_days,
    )

    price_id, trial_days = get_price_id_and_trial_days(plan_id, user=user)

    return await _stripe_checkout(
        user=user,
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        trial_days=trial_days,
    )


async def sync_from_checkout(*, session_id: str, user_id: str) -> User | None:
    """Sync subscription state from a completed Stripe checkout session."""
    from src.domains.billing.services.stripe_service import sync_subscription_from_checkout_session

    return await sync_subscription_from_checkout_session(session_id=session_id, user_id=user_id)


async def create_portal_session(*, user: User, return_url: str) -> dict[str, str]:
    """Create a Stripe customer portal session."""
    from src.domains.billing.services.stripe_service import create_portal_session as _portal

    return await _portal(user=user, return_url=return_url)


async def cancel_subscription(*, user: User) -> dict[str, Any]:
    """Cancel the user's active subscription (provider-aware)."""
    # User is the SQLAlchemy model: attributes are snake_case, columns camelCase.
    provider = user.payment_provider

    if not provider:
        if user.paystack_subscription_code:
            provider = "paystack"
        elif user.stripe_subscription_id:
            provider = "stripe"

    if provider == "paystack":
        from src.domains.billing.services.paystack_service import cancel_paystack_subscription

        result = await cancel_paystack_subscription(user=user)
    else:
        from src.domains.billing.services.stripe_service import (
            cancel_subscription as _stripe_cancel,
        )

        result = await _stripe_cancel(user=user)

    await emit("billing.subscription_cancelled", {"user_id": user.id, "provider": provider})
    return result


async def initialize_paystack(
    *, user: User, plan_id: str, success_url: str, cancel_url: str
) -> dict[str, Any]:
    """Initialize a Paystack subscription (NGN)."""
    from src.domains.billing.services.paystack_service import initialize_paystack_subscription

    return await initialize_paystack_subscription(
        user=user,
        plan_id=plan_id,
        success_url=success_url,
        cancel_url=cancel_url,
    )


async def verify_paystack(*, reference: str, user_id: str) -> User | None:
    """Verify a Paystack transaction after redirect."""
    from src.domains.billing.services.paystack_service import verify_paystack_transaction

    return await verify_paystack_transaction(reference=reference, user_id=user_id)


async def verify_google_play_subscription(
    *, user_id: str, product_id: str, purchase_token: str, base_plan_id: str = ""
) -> dict[str, Any]:
    """Verify a Google Play subscription purchase."""
    from src.domains.billing.services.google_play_service import verify_subscription

    return await verify_subscription(
        user_id=user_id,
        product_id=product_id,
        purchase_token=purchase_token,
        base_plan_id=base_plan_id,
    )


async def verify_google_play_product(
    *, user_id: str, product_id: str, purchase_token: str
) -> dict[str, Any]:
    """Verify a Google Play in-app product (one-time) purchase."""
    from src.domains.billing.services.google_play_service import verify_product_purchase

    return await verify_product_purchase(
        user_id=user_id,
        product_id=product_id,
        purchase_token=purchase_token,
    )
