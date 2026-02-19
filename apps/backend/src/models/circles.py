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
