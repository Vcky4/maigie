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
# ``stripe_service.DEPRECATED_PLAN_IDS`` exists to give.
#
# The two Plus passes are here for the same reason, having originally been omitted on the
# grounds that they are one-time products rather than subscriptions. That was true and still
# produced the wrong behaviour: ``stripe_service.get_price_id_and_trial_days`` carries a
# specific refusal — "that is a one-time Plus pass, use the pass checkout" — and a Literal
# without the ids meant FastAPI answered 422 before the handler ran, so the refusal was
# unreachable and the message never seen. Exactly the failure the paragraph above describes.
PlanId = Literal[
    # Active
    "maigie_plus_monthly",
    "plus_monthly",
    "circle_plan_monthly",
    "plus_seat_add_on_monthly",
    # One-time products — accepted so the refusal names the right door (400, not 422)
    "plus_pass_5h",
    "plus_pass_7d",
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
    # What the product actually buys, in terms a learner recognises. Served rather than
    # composed on the client so that "5 hours of Plus" can never be displayed without the
    # voice caveat beside it — five hours of live tutoring costs roughly eight times what
    # the pass earns, and the sentence has to say so.
    #
    # Carries no consumption figures until Phase 3. It briefly carried the §6.3 window
    # allowances, which the live meter does not implement; see
    # `stripe_service.get_active_plan_catalog` for what was wrong with that and
    # `test_subscription_catalog.TestUsageEquivalents` for what holds the line.
    usage_note: str | None = None
    # Whether the plan applies to one person, to a whole space, or tops up an
    # existing space plan. Clients need this to group the catalog for display.
    scope: Literal["personal", "circle", "add_on"] = "personal"
    # Whether this product can be bought right now.
    #
    # The two Plus passes are in the catalogue from Phase 1 so that clients and generated
    # types can be built against the real shape, but their one-time checkout does not exist
    # until Phase 5. Without this field a client had no way to tell, and the honest reading
    # of a catalogue entry is "you can buy this" — so it would have rendered a Buy button
    # that answers 400. `false` means list it, describe it, and do not offer it.
    purchasable: bool = True


class PlanCatalogResponse(BaseModel):
    """Active product catalog."""

    plans: list[PlanItem]


# ===========================================================================
# Entitlement
# ===========================================================================


class EntitlementResponse(CamelModel):
    """What the learner is entitled to in their personal workspace, right now.

    The serialised form of `entitlement_service.Entitlement`. Clients read this instead of
    inferring entitlement from `User.tier`, which is what every hardcoded `tierLabel` and
    `currentTier="PREMIUM_MONTHLY"` in the web app is currently doing.

    `source` is served rather than derived because the correct UI differs per source and the
    difference is not cosmetic: a subscriber sees a renewal date and a manage-billing link, a pass
    holder sees a countdown and no billing relationship to manage, and a trialling learner sees a day
    counter and a price. `expiresAt` alone cannot tell them apart.
    """

    tier: Literal["free", "plus"]
    source: Literal["none", "subscription", "pass", "trial"]
    expires_at: datetime | None = None
    pass_id: str | None = None
    subscription_tier: str | None = None
    is_trial: bool = False
    trial_days_remaining: int | None = None
    # Deliberately not a unit count on screen (§6.3): the client renders a percentage and a reset
    # time. It is served because the percentage is `used / allowance` and only the server knows the
    # denominator, which changes the moment a pass is activated.
    window_allowance: int


class UsageResponse(CamelModel):
    """How much of the current window is spent, and when it refills.

    **A percentage and a timestamp, never a unit count.** A unit is $0.0001 of our measured COGS
    (§6.2); putting it on screen would leak our cost basis and invite a learner to do arithmetic
    instead of studying. The marketing states checkable equivalents instead — "about 15 minutes of
    live voice tutoring" — which is a promise about their experience rather than about our ledger.

    `monthlyPercentUsed` is absent until the learner is within 20% of the monthly backstop. The
    backstop is an abuse limit, not a product limit (§6.3), and naming a number designed not to bind
    invites planning around it. `monthlyExhausted` is present whenever a backstop applies, because a
    client refused by the *month* must not tell the learner to wait five hours.
    """

    tier: Literal["free", "plus"]
    window_resets_at: datetime
    percent_used: float
    is_exhausted: bool
    monthly_percent_used: float | None = None
    monthly_exhausted: bool | None = None


class VoiceBalanceResponse(CamelModel):
    """Live-voice minutes remaining, and whether voice is available at all.

    **A separate endpoint from `/usage` because it is a separate meter** (§6.3). Voice is not drawn
    from the usage window — at 200 units a minute it is 40× a chat turn, so one allowance covering both
    had to be priced against the voice case and was spent almost entirely on the text case. Folding it
    into `UsageResponse` would put two unrelated clocks behind one percentage.

    **Minutes here, not units and not a percentage**, which is the opposite choice from `/usage` and
    deliberate. A unit is a COGS accounting device that means nothing to a learner, so `/usage` shows a
    proportion. A voice minute is exactly what was sold — "60 minutes a month" — so the honest display
    is the count, and a percentage would obscure the one number the learner was promised.

    `available` is not `minutesRemaining > 0`. A free learner has no voice *capability* and needs to be
    told that voice is part of Plus; a subscriber at zero has run out and needs the top-up. Same empty
    counter, two different screens, so the server decides rather than leaving the client to infer it
    from a zero.
    """

    available: bool
    minutes_remaining: int
    #: Seconds, for a client that renders a countdown mid-session. `minutesRemaining` is the display
    #: figure and rounds **down**, so a learner told "1 minute" who gets 50 seconds is not misled.
    seconds_remaining: int
    #: Included minutes this entitlement grants per period, so a client can render "12 of 60 left"
    #: without a second call. Zero when voice is not part of the plan.
    minutes_included: int
    #: True when any of the balance was bought rather than granted. Purchased minutes do not expire
    #: with the period, and a client that says "resets when your plan renews" would be wrong about them.
    has_purchased_minutes: bool


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
    # `initialize_paystack_subscription` computes both — Paystack has no plan-change API, so an
    # upgrade is cancel-and-resubscribe, and the client needs to know that is what just happened.
    # They were being returned by the service and dropped here, silently, because Pydantic ignores
    # extra keys by default. The Stripe response has carried the equivalent pair since it was
    # written; this is the same information on the other rail.
    is_modification: bool = False
    is_upgrade: bool | None = None


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


# `AdminCreditAdjustRequest`/`AdminCreditAdjustResponse` are gone with the endpoint they
# shaped. `newBalance` named a column that Phase 3 dropped, and there is no balance to
# report once usage is a self-refilling window.


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
