"""
Admin domain — Pydantic request/response schemas.

Platform administration, content management, staff operations.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminUserResponse(BaseModel):
    """User record as seen by admin."""

    id: str
    email: str
    name: str | None = None
    tier: str
    role: str
    isActive: bool
    isOnboarded: bool = False
    adminStaffRole: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminStatsResponse(BaseModel):
    """Platform-level statistics."""

    totalUsers: int
    activeUsers: int
    premiumUsers: int
    totalCourses: int
    totalSpaces: int
    totalMessages: int


class StaffRoleUpdateRequest(BaseModel):
    """Update a user's staff role."""

    userId: str
    staffRole: str  # SUPER_ADMIN | CONTENT_MANAGER


class HealthCheckResponse(BaseModel):
    """Detailed health check for admin."""

    database: dict
    cache: dict
    workers: dict
    version: str
    environment: str
