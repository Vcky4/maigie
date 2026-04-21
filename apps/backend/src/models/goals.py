"""
Goal models for request/response schemas.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.schedule import ScheduleResponse


class GoalStatus(str):
    """Goal status enum values."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    CANCELLED = "CANCELLED"


class GoalCreate(BaseModel):
    """Schema for creating a new goal."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] = Field(default="ACTIVE")
    courseId: str | None = None
    topicId: str | None = None


class GoalUpdate(BaseModel):
    """Schema for updating a goal."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] | None = None
    progress: float | None = Field(None, ge=0.0, le=100.0)
    courseId: str | None = None
    topicId: str | None = None


class GoalProgressUpdate(BaseModel):
    """Schema for recording progress on a goal."""

    progress: float = Field(..., ge=0.0, le=100.0, description="Progress from 0.0 to 100.0")


class GoalResponse(BaseModel):
    """Schema for goal response."""

    id: str
    userId: str
    title: str
    description: str | None
    targetDate: str | None
    status: str
    progress: float
    courseId: str | None = None
    topicId: str | None = None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class GoalListResponse(BaseModel):
    """Schema for paginated goal list response."""

    goals: list[GoalResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class GoalContributionDay(BaseModel):
    date: str
    minutes: float


class GoalContributionSummary(BaseModel):
    last7DaysMinutes: float
    last30DaysMinutes: float
    daily: list[GoalContributionDay]


class GoalStreakSummary(BaseModel):
    currentStreak: int
    longestStreak: int


class GoalDetailResponse(BaseModel):
    goal: GoalResponse
    streak: GoalStreakSummary
    contributions: GoalContributionSummary
    schedules: list[ScheduleResponse]


class GoalRegeneratePlanRequest(BaseModel):
    duration_weeks: int = Field(default=4, ge=1, le=16)
    request: str | None = Field(default=None, max_length=500)


class GoalRegeneratePlanResponse(BaseModel):
    status: Literal["success", "error"]
    goal_id: str
    deleted_schedule_blocks: int = 0
    created_schedule_blocks: int = 0
    target_date: str | None = None
    study_tips: list[str] = Field(default_factory=list)
    message: str | None = None
