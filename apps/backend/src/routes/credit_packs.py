"""
Credit Packs routes for the Credit Purchase System.

This module handles:
- Listing available credit packs with user-specific pricing
- Initiating credit pack purchases
- Viewing purchase history
- Admin credit balance adjustments

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from ..dependencies import AdminUser, CurrentUser
from ..schemas.credit_packs import (
    AdminCreditAdjustRequest,
    AdminCreditAdjustResponse,
    CreditPackResponse,
    PaginatedPurchaseHistory,
    PurchaseInitiateRequest,
    PurchaseSessionResponse,
)
from ..services import credit_purchase_service
from ..utils.exceptions import ResourceNotFoundError, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["credit-packs"])


# =============================================================================
# Credit Pack Catalog
# =============================================================================


@router.get("/credit-packs", response_model=list[CreditPackResponse])
async def get_credit_packs(current_user: CurrentUser):
    """
    List available credit packs with pricing in the user's currency.

    Returns the catalog of active credit packs ordered by ascending credit
    amount. Prices are shown in NGN for Paystack users and USD otherwise.
    """
    try:
        packs = await credit_purchase_service.get_credit_packs(current_user)
        return packs
    except Exception as e:
        logger.error(f"Error fetching credit packs for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve credit packs",
        )


# =============================================================================
# Purchase Flow
# =============================================================================


@router.post("/credit-packs/purchase", response_model=PurchaseSessionResponse)
async def initiate_purchase(
    body: PurchaseInitiateRequest,
    current_user: CurrentUser,
):
    """
    Initiate a credit pack purchase.

    Creates a one-time payment session with the user's configured payment
    provider (Stripe or Paystack). Returns a session URL to redirect the
    user to complete payment.
    """
    try:
        result = await credit_purchase_service.initiate_purchase(
            user=current_user,
            pack_id=body.packId,
            success_url=body.successUrl,
            cancel_url=body.cancelUrl,
        )
        return result
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credit pack not found",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            f"Error initiating purchase for user {current_user.id}, " f"pack {body.packId}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate purchase",
        )


# =============================================================================
# Purchase History
# =============================================================================


@router.get("/credits/purchases", response_model=PaginatedPurchaseHistory)
async def get_purchase_history(
    current_user: CurrentUser,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    Get paginated purchase transaction history for the current user.

    Returns transactions ordered by most recent first with pagination metadata.
    """
    try:
        result = await credit_purchase_service.get_purchase_history(
            user_id=current_user.id,
            page=page,
            page_size=pageSize,
        )
        return result
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.message,
        )
    except Exception as e:
        logger.error(f"Error fetching purchase history for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve purchase history",
        )


# =============================================================================
# Admin Credit Adjustment
# =============================================================================


@router.post("/admin/credits/adjust", response_model=AdminCreditAdjustResponse)
async def admin_adjust_balance(
    body: AdminCreditAdjustRequest,
    admin_user: AdminUser,
):
    """
    Adjust a user's purchased credits balance (admin only).

    Allows granting (positive amount) or deducting (negative amount) credits.
    Creates an audit log entry for the adjustment.
    """
    try:
        updated_user = await credit_purchase_service.admin_adjust_balance(
            admin_id=admin_user.id,
            target_user_id=body.userId,
            amount=body.amount,
            reason=body.reason,
        )
        return AdminCreditAdjustResponse(
            userId=updated_user.id,
            newBalance=updated_user.purchasedCreditsBalance or 0,
            adjustmentAmount=body.amount,
        )
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        )
    except Exception as e:
        logger.error(
            f"Error adjusting credits for user {body.userId} " f"by admin {admin_user.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to adjust credit balance",
        )
