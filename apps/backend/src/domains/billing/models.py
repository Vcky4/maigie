"""
Billing domain — Pydantic request/response schemas.

Covers subscriptions, credits, plans, referrals, and payment webhooks.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.shared.schemas import CamelModel

# ===========================================================================
# Plans
# ===========================================================================

# Accepted at the subscription checkout surface. This deliberately includes ids that are
# **not** sold any more, because a request carrying one has to be distinguishable from a
# request carrying nonsense: a Literal that omitted them would answer 422 "not a valid
# plan" and shadow the 410 "this plan was withdrawn, here is what replaced it" that
# ``stripe_service.DEPRECATED_PLAN_IDS`` exists to give. The two Plus passes are absent
# because they are one-time products, not subscriptions — see ``PASS_PRODUCT_IDS``.
PlanId = Literal[
    # Active
    "maigie_plus_monthly",
    "plus_monthly",
    "circle_plan_monthly",
    "plus_seat_add_on_monthly",
    # Withdrawn — accepted so the refusal can be specific (410, not 422)
    "maigie_plus_yearly",
    "plus_yearly",
    "study_circle_monthly",
    "study_circle_yearly",
    "squad_monthly",
    "squad_yearly",
]


class PlanItem(CamelModel):
    """Single product in the catalog.

    Serialized camelCase, like every other schema written since ``CamelModel`` landed.
    Safe to change here because this endpoint has never been mounted, so no client is
    reading the old snake_case spelling.
    """

    id: str
    name: str
    description: str
    price_cents: int
    currency: str = "usd"
    interval: str  # "none" | "month" | "year" | "one_time"
    trial_days: int = 0
    features: list[str] = []
    # The concrete usage equivalent, in units a learner recognises, for this product.
    # Served rather than composed on the client so that "5 hours of Plus" can never be
    # displayed without the voice figure beside it — five hours of live tutoring costs
    # roughly eight times what the pass earns, and the sentence has to say so.
    usage_note: str | None = None
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


# ===========================================================================
# Credits
# ===========================================================================
#
# The credit-pack catalog and purchase-initiation schemas are gone with the product.
# Credit packs are withdrawn: the unit they sold is being replaced by a usage window,
# and a pack of a unit that no longer exists cannot be priced honestly. The history and
# admin-adjustment schemas below stay — the transactions they describe are real and are
# retained as read-only history.


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
# Ads (Rewarded Video) — withdrawn
# ===========================================================================
#
# Nothing in the product asks a learner to watch an advertisement. The reward these
# schemas described was a daily credit-limit increase, which is invisible: a learner
# cannot see it, predict it, or plan a study session around it, so it bought no goodwill
# and cost real inference. Earning now produces points, and points buy passes — something
# a learner can hold, see, and choose when to spend.
#
# The `AdRewardClaim` table stays in place, empty and unread. Dropping it would foreclose
# a redesign at no saving. If ads return they will be designed as a product decision, not
# inherited as a credit top-up.


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
