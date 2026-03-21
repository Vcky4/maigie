"""
Pydantic models for Circle (study group) management.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Request Models ---


class CircleCreate(BaseModel):
    """Schema for creating a new circle."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


class CircleUpdate(BaseModel):
    """Schema for updating a circle."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    avatarUrl: str | None = None


class CircleChatGroupCreate(BaseModel):
    """Schema for creating a chat group within a circle."""

    name: str = Field(..., min_length=1, max_length=100)


class CircleChatGroupUpdate(BaseModel):
    """Schema for renaming a chat group."""

    name: str = Field(..., min_length=1, max_length=100)


class CircleInviteCreate(BaseModel):
    """Schema for inviting member(s) to a circle."""

    emails: list[EmailStr] = Field(..., min_length=1, max_length=5)


class TransferOwnershipRequest(BaseModel):
    """Schema for transferring circle ownership."""

    newOwnerUserId: str


# --- Response Models ---


class CircleMemberResponse(BaseModel):
    """Response schema for a circle member."""

    id: str
    userId: str
    name: str | None = None
    email: str | None = None
    role: str
    joinedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CircleChatGroupResponse(BaseModel):
    """Response schema for a circle chat group."""

    id: str
    name: str
    circleId: str
    chatSessionId: str | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CircleResponse(BaseModel):
    """Response schema for a circle (list view)."""

    id: str
    name: str
    description: str | None = None
    avatarUrl: str | None = None
    createdById: str
    maxMembers: int
    maxGroups: int
    memberCount: int = 0
    role: str | None = None  # Current user's role in this circle
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CircleActivityDataItem(BaseModel):
    """Activity data point for circle dashboard graph."""

    name: str
    hours: float


class CircleLeaderboardItem(BaseModel):
    """Leaderboard entry for circle dashboard."""

    userId: str
    name: str
    points: int
    role: str


class CircleDetailResponse(BaseModel):
    """Detailed response schema for a circle (detail view)."""

    id: str
    name: str
    description: str | None = None
    avatarUrl: str | None = None
    createdById: str
    maxMembers: int
    maxGroups: int
    members: list[CircleMemberResponse]
    chatGroups: list[CircleChatGroupResponse]
    role: str | None = None  # Current user's role in this circle
    credits: int | None = None
    creditsLimit: int | None = None
    activityData: list[CircleActivityDataItem] = []
    leaderboard: list[CircleLeaderboardItem] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CircleInviteResponse(BaseModel):
    """Response schema for a circle invite."""

    id: str
    circleId: str
    circleName: str | None = None
    inviterId: str
    inviterName: str | None = None
    inviteeEmail: str
    status: str
    expiresAt: datetime
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CircleListResponse(BaseModel):
    """Response schema for listing circles."""

    circles: list[CircleResponse]
    total: int


class CircleImportRequest(BaseModel):
    """Schema for importing items into a circle."""

    resourceIds: list[str] = []
    courseIds: list[str] = []
    noteIds: list[str] = []
    goalIds: list[str] = []


# --- Circle Session Models ---


class CircleSessionCreate(BaseModel):
    """Schema for creating a group session."""

    title: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    scheduledAt: datetime
    duration: int = 60
    chatGroupId: str = Field(..., min_length=1)
    topicId: str | None = None
    goalId: str | None = None


class CircleSessionUpdate(BaseModel):
    """Schema for updating a group session."""

    title: str | None = None
    description: str | None = None
    scheduledAt: datetime | None = None
    duration: int | None = None
    status: str | None = None
    chatGroupId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class CircleSessionResponse(BaseModel):
    """Response schema for a group session."""

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


class CircleSessionSuggestion(BaseModel):
    """A suggested group session generated by AI."""

    title: str
    description: str
    duration: int
    reason: str


class CircleSessionSuggestionResponse(BaseModel):
    """Response schema for suggested sessions."""

    suggestions: list[CircleSessionSuggestion]
