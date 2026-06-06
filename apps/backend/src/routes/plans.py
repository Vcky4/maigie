"""
Plans catalog route — exposes the active product catalog.

GET /api/v1/plans/catalog returns the five active products:
FREE, PLUS_MONTHLY, PLUS_YEARLY, CIRCLE_PLAN_MONTHLY, PLUS_SEAT_ADD_ON_MONTHLY.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from fastapi import APIRouter

from src.schemas.subscription import PlanCatalogResponse
from src.services.subscription_service import get_active_plan_catalog

router = APIRouter(prefix="/api/v1/plans", tags=["plans"])


@router.get("/catalog", response_model=PlanCatalogResponse)
async def plan_catalog() -> PlanCatalogResponse:
    """Return the active product catalog.

    No authentication required — the catalog is public information used
    by pricing pages on web, mobile, and the public marketing site.
    """
    return get_active_plan_catalog()
