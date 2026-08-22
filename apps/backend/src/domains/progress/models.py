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


#: What a goal measures. Mirrors the `Goal_metricKind_check` constraint exactly; a test pins the two
#: against each other, because a value Pydantic accepts and Postgres refuses is a 500 rather than
#: the 422 the learner should get.
GoalMetricKind = Literal[
    "focused_minutes",
    "topics_mastered",
    "cards_reviewed",
    "course_progress",
    "prep_readiness",
    "manual",
]


class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] = "ACTIVE"
    courseId: str | None = None
    topicId: str | None = None
    prepId: str | None = None

    # --- What the goal measures ---
    metricKind: GoalMetricKind = "manual"
    targetValue: float | None = Field(None, ge=0.0)
    unit: str | None = Field(None, max_length=40)
    #: Only meaningful when `metricKind` is `manual`. The service **refuses** it for every other
    #: kind rather than accepting and ignoring it, because a learner who types a current value and
    #: watches it disappear has been silently overruled.
    currentValue: float | None = Field(None, ge=0.0)


class GoalUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    targetDate: datetime | None = None
    status: Literal["ACTIVE", "COMPLETED", "ARCHIVED", "CANCELLED"] | None = None
    progress: float | None = Field(None, ge=0.0, le=100.0)
    courseId: str | None = None
    topicId: str | None = None
    prepId: str | None = None
    metricKind: GoalMetricKind | None = None
    targetValue: float | None = Field(None, ge=0.0)
    unit: str | None = Field(None, max_length=40)
    currentValue: float | None = Field(None, ge=0.0)


class GoalProgressUpdate(BaseModel):
    progress: float = Field(..., ge=0.0, le=100.0)


class GoalMilestoneCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    detail: str | None = Field(None, max_length=2000)
    targetValue: float | None = Field(None, ge=0.0)
    orderIndex: int = Field(0, ge=0)


class GoalMilestoneUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    detail: str | None = Field(None, max_length=2000)
    targetValue: float | None = Field(None, ge=0.0)
    orderIndex: int | None = Field(None, ge=0)
    #: Explicit rather than a `POST .../achieve` toggle, so a milestone reached on Tuesday can be
    #: recorded on Thursday with Tuesday's date. `null` un-achieves it, which a learner who ticked
    #: the wrong row needs.
    achievedAt: datetime | None = None


class GoalMilestoneResponse(BaseModel):
    id: str
    goalId: str
    title: str
    detail: str | None = None
    targetValue: float | None = None
    orderIndex: int = 0
    achievedAt: str | None = None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


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
    prepId: str | None = None

    # --- What the goal measures ---
    metricKind: str = "manual"
    targetValue: float | None = None
    unit: str | None = None
    #: Stored for a `manual` goal, **measured** for every other kind. `null` when the goal's kind
    #: needs a link it does not have — a `course_progress` goal with no `courseId` has nothing to
    #: measure, and null says so where `0` would claim no progress.
    currentValue: float | None = None
    #: `true` when `currentValue` came from event rows rather than from the learner. Published so a
    #: client never has to infer which of the two it is holding, which is the whole reason
    #: `metricKind` exists.
    currentValueMeasured: bool = False

    # --- Derived, never stored ---
    #: `COMPLETED` | `ON_TRACK` | `NEEDS_ATTENTION`. Distinct from `status`, which is the learner's
    #: own lifecycle value. Derived because two of the three are questions about today: a stored
    #: `ON_TRACK` is wrong by tomorrow morning.
    statusLabel: str = "ON_TRACK"
    #: Progress as a share of where the schedule says it should be; 100 is exactly on pace. `null`
    #: without a `targetDate`, and `null` very early in the window where the ratio swings wildly.
    pacePercent: float | None = None
    #: Straight-line projection of where this lands by the deadline, capped at 100.
    projectedOutcome: float | None = None
    milestonesTotal: int = 0
    milestonesAchieved: int = 0

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
