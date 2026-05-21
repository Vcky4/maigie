"""
Pydantic schemas for the Credit Purchase System.

Defines request/response models for credit pack catalog, purchase flow,
purchase history, admin adjustments, and extended credit usage summary.
"""

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Credit Pack Catalog
# =============================================================================


class CreditPackResponse(BaseModel):
    """Response model for a single credit pack in the catalog."""

    id: str
    name: str
    credits: int
    bonusCredits: int
    totalCredits: int  # credits + bonusCredits
    price: int  # In smallest currency unit (cents for USD, kobo for NGN)
    currency: str  # "USD" or "NGN"
    priceFormatted: str  # "$1.99" or "₦3,200"


# =============================================================================
# Purchase Flow
# =============================================================================


class PurchaseInitiateRequest(BaseModel):
    """Request body for initiating a credit pack purchase."""

    packId: str = Field(..., description="ID of the credit pack to purchase")
    successUrl: str = Field(
        ..., description="URL to redirect to after successful payment"
    )
    cancelUrl: str = Field(
        ..., description="URL to redirect to if payment is cancelled"
    )


class PurchaseSessionResponse(BaseModel):
    """Response after initiating a purchase session."""

    sessionUrl: str  # Redirect URL for payment
    sessionId: str  # For tracking
    expiresAt: str  # ISO datetime


# =============================================================================
# Purchase History
# =============================================================================


class PurchaseTransactionResponse(BaseModel):
    """Response model for a single purchase transaction."""

    id: str
    creditPackName: str
    creditsGranted: int
    amountPaid: int
    currency: str
    priceFormatted: str
    status: str
    completedAt: str | None
    createdAt: str


class PaginatedPurchaseHistory(BaseModel):
    """Paginated list of purchase transactions."""

    items: list[PurchaseTransactionResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


# =============================================================================
# Admin Credit Adjustment
# =============================================================================


class AdminCreditAdjustRequest(BaseModel):
    """Request body for admin credit balance adjustment."""

    userId: str = Field(..., description="Target user ID to adjust credits for")
    amount: int = Field(
        ...,
        description="Adjustment amount. Positive = grant, negative = deduct. Must be non-zero.",
    )
    reason: str = Field(
        ..., description="Admin-provided reason for the adjustment"
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Adjustment amount must be non-zero")
        if abs(v) > 999_999_999:
            raise ValueError(
                "Adjustment amount must be between -999999999 and 999999999"
            )
        return v


class AdminCreditAdjustResponse(BaseModel):
    """Response after a successful admin credit adjustment."""

    userId: str
    newBalance: int
    adjustmentAmount: int


# =============================================================================
# Extended Credit Usage Summary
# =============================================================================


class CreditUsageSummaryExtended(BaseModel):
    """Extended credit usage summary including purchased credits balance.

    Extends the existing usage response with purchased credits information.
    """

    # Existing subscription credit fields
    creditsUsed: int
    creditsRemaining: int
    hardCap: int
    softCap: int
    usagePercentage: float
    isSoftCapReached: bool
    isHardCapReached: bool

    # Daily usage (for FREE tier)
    creditsUsedToday: int | None = None
    creditsRemainingToday: int | None = None
    dailyLimit: int | None = None
    dailyUsagePercentage: float | None = None
    isDailyLimitReached: bool | None = None
    nextDailyReset: str | None = None

    # Purchased credits extension
    purchasedCreditsBalance: int
    totalAvailable: int  # subscription remaining + purchased
