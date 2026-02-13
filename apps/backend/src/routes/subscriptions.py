"""
Subscription routes for Stripe integration.

This module handles subscription-related endpoints including:
- Creating checkout sessions
- Managing subscriptions via customer portal
- Canceling subscriptions

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..dependencies import CurrentUser
from ..services.subscription_service import (
    cancel_subscription,
    create_checkout_session,
    get_price_id_and_trial_days,
)
from ..services.subscription_service import (
    create_portal_session as create_stripe_portal_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subscriptions"])

# Valid plan IDs for checkout
PlanId = Literal[
    "maigie_plus_monthly",
    "maigie_plus_yearly",
    "study_circle_monthly",
    "study_circle_yearly",
    "squad_monthly",
    "squad_yearly",
]


class CheckoutSessionRequest(BaseModel):
    """Request model for creating checkout session."""

    plan_id: PlanId = Field(
        ...,
        description="Plan identifier (e.g. maigie_plus_monthly, squad_yearly)",
    )


class CheckoutSessionResponse(BaseModel):
    """Response model for checkout session."""

    session_id: str
    url: str | None = None
    modified: bool = False
    is_upgrade: bool | None = None
    current_period_end: str | None = None


class PortalSessionResponse(BaseModel):
    """Response model for portal session."""

    url: str


class CancelSubscriptionResponse(BaseModel):
    """Response model for cancel subscription."""

    status: str
    cancel_at_period_end: bool
    current_period_end: str


@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_subscription_checkout(
    request: CheckoutSessionRequest,
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Create a Stripe checkout session for subscription.

    Plans: Maigie Plus (7-day trial), Study Circle (3-day trial), Squad Plan (3-day trial).
    Each plan has monthly and yearly options.

    Args:
        request: Checkout session request with plan_id
        current_user: Current authenticated user
        http_request: FastAPI request object
        settings: Application settings

    Returns:
        Checkout session with URL
    """
    try:
        price_id, trial_days = get_price_id_and_trial_days(request.plan_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Build success and cancel URLs
    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    success_url = f"{base_url}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/subscription/cancel"

    try:
        session_data = await create_checkout_session(
            user=current_user,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            trial_days=trial_days,
        )

        # If subscription was modified directly (not through checkout)
        if session_data.get("modified"):
            return CheckoutSessionResponse(
                session_id=session_data["session_id"],
                url=None,
                modified=True,
                is_upgrade=session_data.get("is_upgrade"),
                current_period_end=session_data.get("current_period_end"),
            )

        return CheckoutSessionResponse(**session_data)

    except ValueError as e:
        # Handle validation errors (e.g., already on same plan)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session",
        )


@router.post("/portal", response_model=PortalSessionResponse)
async def create_portal_session(
    current_user: CurrentUser,
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Create a Stripe customer portal session for subscription management.

    Args:
        current_user: Current authenticated user
        http_request: FastAPI request object
        settings: Application settings

    Returns:
        Portal session URL
    """
    if not current_user.stripeCustomerId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a Stripe customer account",
        )

    base_url = settings.FRONTEND_URL or str(http_request.base_url).rstrip("/")
    return_url = f"{base_url}/subscription"

    try:
        session_data = await create_stripe_portal_session(
            user=current_user,
            return_url=return_url,
        )

        return PortalSessionResponse(**session_data)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error creating portal session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create portal session",
        )


@router.post("/cancel", response_model=CancelSubscriptionResponse)
async def cancel_user_subscription(current_user: CurrentUser):
    """
    Cancel the current user's subscription.

    The subscription will remain active until the end of the current billing period.

    Args:
        current_user: Current authenticated user

    Returns:
        Subscription cancellation details
    """
    if not current_user.stripeSubscriptionId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an active subscription",
        )

    try:
        result = await cancel_subscription(user=current_user)

        return CancelSubscriptionResponse(
            status=result["status"],
            cancel_at_period_end=result["cancel_at_period_end"],
            current_period_end=result["current_period_end"].isoformat(),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error canceling subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription",
        )
