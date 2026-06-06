"""
Pydantic schemas for the reimagined subscription / plan catalog.

Defines the public-facing tier and product enums used by the plan catalog
endpoint and by client code. Storage continues to use the existing
``Tier`` Prisma enum (``PREMIUM_MONTHLY`` / ``PREMIUM_YEARLY``); the
API surface uses the user-facing names (``PLUS_MONTHLY`` /
``PLUS_YEARLY``) and converts at the boundary via the helpers below.

Reference: design.md "Research Notes" — `Tier` enum currently includes
``PREMIUM_*``, ``STUDY_CIRCLE_*``, ``SQUAD_*``. The Plus tiers are stored
as ``PREMIUM_*`` and rendered as "Maigie Plus" in UI; we keep that
storage representation and expose ``PLUS_MONTHLY`` / ``PLUS_YEARLY``
aliases at the API surface to avoid an enum rename migration.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

# =============================================================================
# Personal subscription tiers (API-surface enum)
# =============================================================================


class PersonalTier(str, Enum):
    """The three personal subscription tiers exposed to clients.

    The ``PLUS_*`` values are the user-facing API aliases that map to the
    existing ``PREMIUM_*`` storage values in the ``Tier`` Prisma enum.
    """

    FREE = "FREE"
    PLUS_MONTHLY = "PLUS_MONTHLY"
    PLUS_YEARLY = "PLUS_YEARLY"


# Storage-layer aliases.
#
# These map the new public API values (``PLUS_MONTHLY`` / ``PLUS_YEARLY``)
# to the existing storage enum values (``PREMIUM_MONTHLY`` /
# ``PREMIUM_YEARLY``). They are intentionally string constants so they
# can be used in ``where={"tier": ...}`` filters without importing the
# Prisma enum.
PLUS_MONTHLY_STORAGE_VALUE: Literal["PREMIUM_MONTHLY"] = "PREMIUM_MONTHLY"
PLUS_YEARLY_STORAGE_VALUE: Literal["PREMIUM_YEARLY"] = "PREMIUM_YEARLY"

# Bidirectional maps between API tier values and storage tier values.
API_TIER_TO_STORAGE_TIER: dict[str, str] = {
    "FREE": "FREE",
    "PLUS_MONTHLY": PLUS_MONTHLY_STORAGE_VALUE,
    "PLUS_YEARLY": PLUS_YEARLY_STORAGE_VALUE,
}

STORAGE_TIER_TO_API_TIER: dict[str, str] = {
    "FREE": "FREE",
    PLUS_MONTHLY_STORAGE_VALUE: "PLUS_MONTHLY",
    PLUS_YEARLY_STORAGE_VALUE: "PLUS_YEARLY",
}


def storage_tier_to_api_tier(storage_tier: str) -> str:
    """Convert a storage tier value to its API-surface equivalent.

    Deprecated tiers (``STUDY_CIRCLE_*``, ``SQUAD_*``) are surfaced as
    ``FREE`` to clients because they are excluded from the active product
    catalog. Migration will convert real users off those tiers; this
    fallback ensures the API never returns a deprecated value.
    """
    return STORAGE_TIER_TO_API_TIER.get(storage_tier, "FREE")


def api_tier_to_storage_tier(api_tier: str) -> str:
    """Convert an API tier value to its storage equivalent.

    Raises ``ValueError`` for unknown tiers; deprecated tiers are not
    accepted at the API surface.
    """
    if api_tier not in API_TIER_TO_STORAGE_TIER:
        raise ValueError(f"Unknown API tier: {api_tier}")
    return API_TIER_TO_STORAGE_TIER[api_tier]


# =============================================================================
# Plan catalog products
# =============================================================================


class PlanCatalogProductId(str, Enum):
    """The exhaustive set of products in the active plan catalog.

    Per Requirement 1.10, ``GET /plans/catalog`` returns exactly these five
    entries — no STUDY_CIRCLE_* or SQUAD_* entries.
    """

    FREE = "FREE"
    PLUS_MONTHLY = "PLUS_MONTHLY"
    PLUS_YEARLY = "PLUS_YEARLY"
    CIRCLE_PLAN_MONTHLY = "CIRCLE_PLAN_MONTHLY"
    PLUS_SEAT_ADD_ON_MONTHLY = "PLUS_SEAT_ADD_ON_MONTHLY"


class PlanCatalogScope(str, Enum):
    """Whether a catalog product applies to a personal account or a Circle."""

    PERSONAL = "PERSONAL"
    CIRCLE = "CIRCLE"
    ADD_ON = "ADD_ON"


class PlanCatalogEntry(BaseModel):
    """One product in the active plan catalog response."""

    productId: PlanCatalogProductId
    displayName: str
    scope: PlanCatalogScope
    priceCents: int
    currency: str = "USD"
    interval: Literal["MONTH", "YEAR", "ONE_TIME", "NONE"] = "MONTH"
    trialDays: int = 0
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PlanCatalogResponse(BaseModel):
    """Response payload for ``GET /plans/catalog``."""

    products: list[PlanCatalogEntry]
