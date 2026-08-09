"""
Progress domain — Pydantic request/response schemas.

Covers goals, study schedules (StudyBlocks), streaks, achievements,
spaced repetition, analytics, and study sessions.

The Maigie Book metric model: Activity → Progress → Achievement.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ===========================================================================
# Goals
# ===========================================================================


class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] = "ACTIVE"
    courseId: str | None = None
    topicId: str | None = None


class GoalUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] | None = None
    progress: float | None = Field(None, ge=0.0, le=100.0)
    courseId: str | None = None
    topicId: str | None = None


class GoalProgressUpdate(BaseModel):
    progress: float = Field(..., ge=0.0, le=100.0)


class GoalResponse(BaseModel):
    id: str
    userId: str
    title: str
    description: str | None = None
    targetDate: str | None = None
    status: str
    progress: float = 0.0
    courseId: str | None = None
    topicId: str | None = None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class GoalListResponse(BaseModel):
    goals: list[GoalResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class GoalRegeneratePlanRequest(BaseModel):
    duration_weeks: int = Field(default=4, ge=1, le=16)
    request: str | None = Field(default=None, max_length=500)


class GoalRegeneratePlanResponse(BaseModel):
    status: Literal["success", "error"]
    goal_id: str
    deleted_schedule_blocks: int = 0
    created_schedule_blocks: int = 0
    target_date: str | None = None
    study_tips: list[str] = []
    message: str | None = None


# ===========================================================================
# Study Blocks (Schedule)
# ===========================================================================


class StudyBlockCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    startAt: datetime
    endAt: datetime
    recurringRule: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class StudyBlockUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    startAt: datetime | None = None
    endAt: datetime | None = None
    recurringRule: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class StudyBlockResponse(BaseModel):
    id: str
    userId: str
    title: str
    description: str | None = None
    startAt: str
    endAt: str
    recurringRule: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None
    reviewItemId: str | None = None
    googleCalendarEventId: str | None = None
    googleCalendarSyncedAt: str | None = None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class StudyBlockListResponse(BaseModel):
    schedules: list[StudyBlockResponse]
    byDate: dict[str, list[StudyBlockResponse]] = {}
    total: int
    page: int
    pageSize: int
    hasMore: bool


# ===========================================================================
# Study Sessions (Activity tracking)
# ===========================================================================


class StartSessionRequest(BaseModel):
    courseId: str | None = None
    topicId: str | None = None


class SessionResponse(BaseModel):
    sessionId: str
    startTime: str
    endTime: str | None = None
    duration: float | None = None
    message: str | None = None


# ===========================================================================
# Streaks
# ===========================================================================


class StreakResponse(BaseModel):
    currentStreak: int = 0
    longestStreak: int = 0
    lastStudyDate: str | None = None


# ===========================================================================
# Achievements
# ===========================================================================


class AchievementResponse(BaseModel):
    id: str
    type: str
    title: str
    description: str
    icon: str | None = None
    unlockedAt: str
    metadata: dict | None = None

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Spaced Repetition (Review Schedules)
# ===========================================================================


class ReviewScheduleResponse(BaseModel):
    """A spaced repetition review schedule for a topic."""

    id: str
    userId: str
    topicId: str
    topicTitle: str | None = None
    nextReviewAt: datetime
    intervalDays: int
    repetitionCount: int
    easeFactor: float
    lastQuality: int
    lapseCount: int
    lastReviewedAt: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ReviewQualityRequest(BaseModel):
    """Submit review quality (SM-2 algorithm, 0-5 scale)."""

    quality: int = Field(..., ge=0, le=5)


# ===========================================================================
# Analytics
# ===========================================================================


class DailyStudyData(BaseModel):
    date: str
    minutes: float
    sessions: int = 0


class StreakSummary(BaseModel):
    currentStreak: int = 0
    longestStreak: int = 0
    lastStudyDate: str | None = None


class StudyAnalyticsResponse(BaseModel):
    daily: list[DailyStudyData] = []
    streak: StreakSummary
    totalMinutesThisWeek: float = 0.0
    totalMinutesThisMonth: float = 0.0
    totalSessions: int = 0
    averageSessionMinutes: float = 0.0
