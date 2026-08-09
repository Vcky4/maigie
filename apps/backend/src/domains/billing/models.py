"""
Billing domain — Pydantic request/response schemas.

Covers subscriptions, credits, plans, referrals, ads, and payment webhooks.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ===========================================================================
# Plans
# ===========================================================================

PlanId = Literal[
    "maigie_plus_monthly",
    "maigie_plus_yearly",
    "plus_monthly",
    "plus_yearly",
    "circle_plan_monthly",
    "plus_seat_add_on_monthly",
]


class PlanItem(BaseModel):
    """Single plan in the catalog."""

    id: str
    name: str
    description: str
    price_cents: int
    currency: str = "usd"
    interval: str  # "none" | "month" | "year" | "one_time"
    trial_days: int = 0
    features: list[str] = []
    # Whether the plan applies to one person, to a whole space, or tops up an
    # existing space plan. Clients need this to group the catalog for display.
    scope: Literal["personal", "circle", "add_on"] = "personal"


class PlanCatalogResponse(BaseModel):
    """Active product catalog."""

    plans: list[PlanItem]


# ===========================================================================
# Subscriptions
# ===========================================================================


class CheckoutRequest(BaseModel):
    """Create Stripe checkout session."""

    plan_id: PlanId


class CheckoutResponse(BaseModel):
    """Checkout session result."""

    session_id: str
    url: str | None = None
    modified: bool = False
    is_upgrade: bool | None = None
    current_period_end: str | None = None


class SyncCheckoutRequest(BaseModel):
    """Sync subscription from completed checkout."""

    session_id: str = Field(..., description="Stripe checkout session ID (cs_xxx)")


class PortalResponse(BaseModel):
    """Stripe customer portal URL."""

    url: str


class CancelSubscriptionResponse(BaseModel):
    """Subscription cancellation result."""

    status: str
    cancel_at_period_end: bool
    current_period_end: str


# ===========================================================================
# Paystack
# ===========================================================================


class PaystackInitializeRequest(BaseModel):
    """Initialize Paystack subscription."""

    plan_id: PlanId
    success_url: str = ""
    cancel_url: str = ""


class PaystackInitializeResponse(BaseModel):
    """Paystack initialization result."""

    authorization_url: str
    access_code: str | None = None
    reference: str | None = None


class PaystackVerifyResponse(BaseModel):
    """Paystack verification result."""

    tier: str
    paystack_subscription_code: str | None = None


# ===========================================================================
# Google Play
# ===========================================================================


class GooglePlayVerifyRequest(BaseModel):
    """Verify Google Play subscription purchase."""

    productId: str
    purchaseToken: str
    basePlanId: str = ""


class GooglePlayVerifyResponse(BaseModel):
    """Google Play verification result."""

    verified: bool
    tier: str
    expiresAt: str
    startedAt: str
    autoRenewing: bool


class GooglePlayProductVerifyRequest(BaseModel):
    """Verify Google Play in-app product (one-time) purchase."""

    productId: str
    purchaseToken: str


class GooglePlayProductVerifyResponse(BaseModel):
    """Google Play product verification result."""

    verified: bool
    credits: int
    newBalance: int


# ===========================================================================
# Credits
# ===========================================================================


class CreditPackResponse(BaseModel):
    """Credit pack in the catalog."""

    id: str
    name: str
    credits: int
    price_cents: int
    currency: str
    description: str | None = None
    popular: bool = False


class PurchaseInitiateRequest(BaseModel):
    """Initiate a credit pack purchase."""

    packId: str = Field(..., alias="packId")
    successUrl: str = Field("", alias="successUrl")
    cancelUrl: str = Field("", alias="cancelUrl")

    model_config = ConfigDict(populate_by_name=True)


class PurchaseSessionResponse(BaseModel):
    """Credit pack purchase session."""

    session_url: str
    session_id: str
    provider: str  # "stripe" | "paystack"


class PurchaseHistoryItem(BaseModel):
    """Single credit purchase transaction."""

    id: str
    packName: str
    credits: int
    amount: int
    currency: str
    provider: str
    status: str
    createdAt: datetime


class PaginatedPurchaseHistory(BaseModel):
    """Paginated purchase history."""

    items: list[PurchaseHistoryItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


class AdminCreditAdjustRequest(BaseModel):
    """Admin credit balance adjustment."""

    userId: str
    amount: int = Field(..., description="Positive to grant, negative to deduct")
    reason: str


class AdminCreditAdjustResponse(BaseModel):
    """Admin credit adjustment result."""

    userId: str
    newBalance: int
    adjustmentAmount: int


# ===========================================================================
# Referrals
# ===========================================================================


class ReferralStatsResponse(BaseModel):
    """Referral statistics."""

    referralCode: str
    totalReferrals: int
    claimedRewards: int
    unclaimedRewards: int
    totalTokensEarned: int
    totalTokensClaimed: int


class ClaimableRewardResponse(BaseModel):
    """A single claimable referral reward."""

    id: str
    rewardType: str
    tokens: int
    referredUser: dict
    createdAt: str | None = None


class ClaimRewardRequest(BaseModel):
    """Claim a referral reward."""

    rewardId: str


class ClaimRewardResponse(BaseModel):
    """Claim result."""

    rewardId: str
    tokensClaimed: int
    claimDate: str
    dailyLimitIncrease: int


# ===========================================================================
# Ads (Rewarded Video)
# ===========================================================================


class AdRewardRequest(BaseModel):
    """Claim an ad reward."""

    adType: str
    rewardAmount: int
    adUnitId: str | None = None


class AdRewardResponse(BaseModel):
    """Ad reward claim result."""

    credited: int
    adsWatchedToday: int
    remainingToday: int
    dailyLimitIncrease: int


class AdStatsResponse(BaseModel):
    """Ad watch statistics."""

    adsWatchedToday: int
    maxPerDay: int
    remainingToday: int
    creditsPerAd: int
    totalEarned: int


# ===========================================================================
# Learning Space Billing (Circle Plan / Seat Add-ons)
# ===========================================================================


class SeatAddonPurchaseRequest(BaseModel):
    """Purchase Plus Seat add-ons for a Learning Space."""

    quantity: int = Field(default=1, ge=1, le=50)


# ===========================================================================
# Generic
# ===========================================================================


class MessageResponse(BaseModel):
    """Generic success message."""

    message: str
