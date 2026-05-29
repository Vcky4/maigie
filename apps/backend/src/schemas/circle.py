"""
Pydantic schemas for the reimagined Circle product.

Defines request/response models for Circle visibility, per-(User, Circle)
seat tier, the public Circle Repository, seat management, Circle Plan and
Plus Seat add-on billing results, content reports, and the one-time data
migration runner.

These schemas back the routes added by the Circle Reimagining feature
(seats, billing, repository, reports, migration). Existing Circle CRUD
schemas in ``apps/backend/src/models/circles.py`` continue to power the
legacy circle endpoints until those endpoints are migrated to the new
shapes defined here.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class CircleVisibility(str, Enum):
    """Visibility of a Circle.

    PUBLIC Circles appear in the Circle Repository and are joinable per the
    Circle's joinPolicy. PRIVATE Circles are joinable only via valid invite
    link or accepted email invite.
    """

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class SeatTier(str, Enum):
    """Per-(User, Circle) capability tier.

    FREE_SEAT applies Personal Free AI gates to that User's usage scoped to
    the Circle. PLUS_SEAT applies Personal Plus AI gates to that same scope.
    Independent of the User's Personal_Tier and from Seat_Tier in any other
    Circle.
    """

    FREE_SEAT = "FREE_SEAT"
    PLUS_SEAT = "PLUS_SEAT"


class JoinPolicy(str, Enum):
    """How a Public Circle handles join attempts."""

    AUTO_JOIN = "AUTO_JOIN"
    REQUEST_TO_JOIN = "REQUEST_TO_JOIN"


class ReportTargetType(str, Enum):
    """Target categories for moderation reports."""

    CIRCLE = "CIRCLE"
    MEMBER = "MEMBER"
    MESSAGE = "MESSAGE"
    RESOURCE = "RESOURCE"
    PROFILE_IMAGE = "PROFILE_IMAGE"


class CircleSubscriptionStatus(str, Enum):
    """Status of a Circle Plan subscription."""

    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"


class SeatAddonStatus(str, Enum):
    """Status of a Plus Seat add-on subscription."""

    ACTIVE = "ACTIVE"
    CANCELED_AT_PERIOD_END = "CANCELED_AT_PERIOD_END"
    CANCELED = "CANCELED"


class MigrationStatus(str, Enum):
    """Status of a migration run."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# =============================================================================
# Circle Repository / Discovery
# =============================================================================


class CirclePublicListItem(BaseModel):
    """One item in a paginated Circle Repository listing.

    Returned only for Circles with ``visibility == PUBLIC`` and not hidden
    by upheld moderation.
    """

    id: str
    name: str
    description: str | None = None
    category: str | None = None
    avatarUrl: str | None = None
    bannerUrl: str | None = None
    memberCount: int = 0
    featured: bool = False

    model_config = ConfigDict(from_attributes=True)


class CircleDetail(BaseModel):
    """Detailed Circle response for owner / admin / member views.

    Includes plan and seat-pool snapshot fields used by the Circle settings,
    members tab, billing tab, and seat management UI.
    """

    id: str
    name: str
    description: str | None = None
    category: str | None = None
    avatarUrl: str | None = None
    bannerUrl: str | None = None
    visibility: CircleVisibility
    circlePlanActive: bool = False
    circlePlanCurrentPeriodEnd: datetime | None = None
    seatPoolSize: int = 0
    assignedSeatCount: int = 0
    hiddenByModeration: bool = False
    allowMemberExport: bool = False
    featured: bool = False
    joinPolicy: JoinPolicy = JoinPolicy.AUTO_JOIN
    memberCount: int = 0
    createdById: str
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Circle Create / Update
# =============================================================================


class CircleCreateRequest(BaseModel):
    """Request body for creating a Circle.

    Visibility defaults to PRIVATE when omitted (Requirement 4.3).
    """

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=64)
    visibility: CircleVisibility = CircleVisibility.PRIVATE


class CircleUpdateRequest(BaseModel):
    """Request body for updating a Circle.

    Banner, theme, and (per the moderator gate) join policy / featured
    eligibility may be restricted at the route layer based on the Circle's
    plan state. This schema only captures the wire shape.
    """

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=64)
    avatarUrl: str | None = None
    bannerUrl: str | None = None
    themeJson: dict[str, Any] | None = None
    visibility: CircleVisibility | None = None
    allowMemberExport: bool | None = None
    joinPolicy: JoinPolicy | None = None


# =============================================================================
# Seats
# =============================================================================


class SeatRecord(BaseModel):
    """One PLUS_SEAT in a Circle's seat pool.

    ``backedByAddonId`` is None when the seat is one of the four seats
    included in an active Circle Plan; otherwise it references the
    ``CircleSeatAddon`` row that backs the seat.
    """

    seatIndex: int = Field(..., ge=1, description="1-based index within the seat pool")
    assignedToUserId: str | None = None
    assignedToName: str | None = None
    seatTier: SeatTier = SeatTier.PLUS_SEAT
    backedByAddonId: str | None = None
    assignedAt: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SeatListResponse(BaseModel):
    """Response for listing all PLUS_SEATS in a Circle (owner/admin only)."""

    circleId: str
    seatPoolSize: int
    assignedSeatCount: int
    circlePlanActive: bool
    seats: list[SeatRecord]


class SeatAssignRequest(BaseModel):
    """Request body for assigning or unassigning a PLUS_SEAT to a member."""

    target_user_id: str = Field(
        ..., description="ID of the Circle_Member to assign / unassign a PLUS_SEAT for"
    )


class SeatReassignRequest(BaseModel):
    """Request body for atomically reassigning a PLUS_SEAT between two members."""

    from_user_id: str = Field(..., description="Member currently holding the PLUS_SEAT")
    to_user_id: str = Field(..., description="Member to receive the PLUS_SEAT")


# =============================================================================
# Circle Billing
# =============================================================================


class CirclePlanPurchaseResult(BaseModel):
    """Result of a Circle Plan purchase or activation.

    Reflects the post-operation snapshot of the Circle's seat pool and plan
    state. The 4 plan-included seats are reflected in ``seatPoolSize``.
    """

    circleId: str
    subscriptionId: str
    status: CircleSubscriptionStatus
    currentPeriodEnd: datetime
    trialEndsAt: datetime | None = None
    seatPoolSize: int
    circlePlanActive: bool


class SeatAddonResult(BaseModel):
    """Result of a Plus Seat add-on purchase or cancellation.

    ``seatPoolSize`` is the snapshot of the pool size after the operation.
    For period-end cancellations the pool size does not change until the
    period actually ends.
    """

    circleId: str
    addonId: str
    status: SeatAddonStatus
    currentPeriodEnd: datetime
    quantity: int = Field(default=1, ge=1)
    seatPoolSize: int


# =============================================================================
# Reports / Moderation
# =============================================================================


class ReportSubmitRequest(BaseModel):
    """Request body for submitting a moderation report."""

    target_type: ReportTargetType
    target_id: str = Field(..., min_length=1)
    reason_code: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(None, max_length=1000)


# =============================================================================
# Migration
# =============================================================================


class MigrationReport(BaseModel):
    """Summary report produced by the migration runner.

    Per Requirement 17.7, the report exposes the totals an operator needs
    to verify a successful run and to guide manual follow-up for any
    Circles flagged for review.
    """

    runId: str
    startedAt: datetime
    finishedAt: datetime | None = None
    status: MigrationStatus
    circlesMigrated: int = Field(default=0, ge=0)
    circlesFlaggedForManualReview: int = Field(default=0, ge=0)
    usersConvertedFromStudyCircle: int = Field(default=0, ge=0)
    usersConvertedFromSquad: int = Field(default=0, ge=0)
    complimentaryCirclePlanGrants: int = Field(default=0, ge=0)
    dryRun: bool = False


# =============================================================================
# Re-exports / convenience aliases
# =============================================================================

# Literal aliases for places (e.g. route signatures) that prefer Literal
# types to Enum classes.
CircleVisibilityLiteral = Literal["PUBLIC", "PRIVATE"]
SeatTierLiteral = Literal["FREE_SEAT", "PLUS_SEAT"]
