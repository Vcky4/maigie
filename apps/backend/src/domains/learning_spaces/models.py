"""
Learning Spaces domain — Pydantic request/response schemas.

Learning Spaces are collaborative learning environments (previously "Circles").
This domain handles space CRUD, membership, invitations, chat groups,
sessions, seats, visibility, and knowledge import.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ===========================================================================
# Space CRUD
# ===========================================================================


class SpaceCreate(BaseModel):
    """Create a new Learning Space."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    creditsLimit: int | None = Field(None, ge=0)
    visibility: str | None = Field(None, description="PUBLIC or PRIVATE (default PRIVATE)")
    category: str | None = Field(None, max_length=64)


class SpaceUpdate(BaseModel):
    """Update Learning Space settings."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    avatarUrl: str | None = None
    creditsLimit: int | None = Field(None, ge=0)


# ===========================================================================
# Membership
# ===========================================================================


class SpaceMemberResponse(BaseModel):
    """A member within a Learning Space."""

    id: str
    userId: str
    name: str | None = None
    email: str | None = None
    role: str  # OWNER, ADMIN, EDUCATOR (was TUTOR), LEARNER (was MEMBER)
    joinedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteRequest(BaseModel):
    """Invite members to a Learning Space."""

    emails: list[EmailStr] = Field(..., min_length=1, max_length=5)
    role: str | None = Field(
        None, description="Role to assign: LEARNER, EDUCATOR, or ADMIN (default LEARNER)"
    )
    seat_tier: str | None = Field(
        None, description="Seat tier: FREE_SEAT or PLUS_SEAT (default FREE_SEAT)"
    )


class InviteResponse(BaseModel):
    """Invitation details."""

    id: str
    circleId: str  # Will rename to spaceId in schema migration
    circleName: str | None = None
    inviterId: str
    inviterName: str | None = None
    inviteeEmail: str
    status: str
    expiresAt: datetime
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class TransferOwnershipRequest(BaseModel):
    """Transfer space ownership to another member."""

    newOwnerUserId: str


# ===========================================================================
# Chat Groups (Discussions)
# ===========================================================================


class ChatGroupCreate(BaseModel):
    """Create a discussion group within a Learning Space."""

    name: str = Field(..., min_length=1, max_length=100)
    visibility: str = Field(default="PUBLIC", description="PUBLIC or PRIVATE")
    description: str | None = Field(None, max_length=500)
    memberIds: list[str] | None = None


class ChatGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    visibility: str | None = None
    description: str | None = Field(None, max_length=500)


class ChatGroupResponse(BaseModel):
    id: str
    name: str
    circleId: str
    chatSessionId: str | None = None
    visibility: str = "PUBLIC"
    description: str | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Sessions (Group Learning)
# ===========================================================================


class SessionCreate(BaseModel):
    """Schedule a group learning session."""

    title: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    scheduledAt: datetime
    duration: int = 60
    chatGroupId: str = Field(..., min_length=1)
    topicId: str | None = None
    goalId: str | None = None


class SessionUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    scheduledAt: datetime | None = None
    duration: int | None = None
    status: Literal["SCHEDULED", "ACTIVE", "COMPLETED", "CANCELLED"] | None = None
    chatGroupId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class SessionResponse(BaseModel):
    id: str
    circleId: str
    title: str
    description: str | None = None
    scheduledAt: datetime
    duration: int
    status: str
    chatGroupId: str | None = None
    topicId: str | None = None
    goalId: str | None = None
    createdById: str
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionSuggestion(BaseModel):
    """AI-suggested group session."""

    title: str
    description: str
    duration: int
    reason: str


class SessionSuggestionResponse(BaseModel):
    suggestions: list[SessionSuggestion]


# ===========================================================================
# Seats
# ===========================================================================


class SeatAssignRequest(BaseModel):
    """Assign/unassign a Plus Seat."""

    target_user_id: str


class SeatReassignRequest(BaseModel):
    """Reassign a Plus Seat between members."""

    from_user_id: str
    to_user_id: str


# ===========================================================================
# Space Responses
# ===========================================================================


class SpaceResponse(BaseModel):
    """Learning Space list view."""

    id: str
    name: str
    description: str | None = None
    avatarUrl: str | None = None
    createdById: str
    maxMembers: int
    maxGroups: int
    memberCount: int = 0
    role: str | None = None  # Current user's role
    credits: int | None = None
    creditsLimit: int | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class ActivityDataItem(BaseModel):
    name: str
    hours: float


class LeaderboardItem(BaseModel):
    userId: str
    name: str
    points: int
    role: str


class SpaceDetailResponse(BaseModel):
    """Learning Space detail view with members, groups, courses."""

    id: str
    name: str
    description: str | None = None
    avatarUrl: str | None = None
    createdById: str
    maxMembers: int
    maxGroups: int
    members: list[SpaceMemberResponse] = []
    chatGroups: list[ChatGroupResponse] = []
    courses: list[dict] = []
    role: str | None = None
    credits: int | None = None
    creditsLimit: int | None = None
    activityData: list[ActivityDataItem] = []
    leaderboard: list[LeaderboardItem] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class SpaceListResponse(BaseModel):
    """Paginated list of Learning Spaces."""

    spaces: list[SpaceResponse]
    total: int


# ===========================================================================
# Import
# ===========================================================================


class ImportRequest(BaseModel):
    """Import personal items into a Learning Space."""

    resourceIds: list[str] = []
    courseIds: list[str] = []
    noteIds: list[str] = []
    goalIds: list[str] = []
