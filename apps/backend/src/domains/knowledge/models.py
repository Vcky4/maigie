"""
Knowledge domain — Pydantic request/response schemas.

Covers courses, modules, topics, resources, resource bank,
AI generation, and progress analytics.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Enums
# ===========================================================================


class DifficultyLevel(str, Enum):
    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    EXPERT = "EXPERT"


class ResourceType(str, Enum):
    VIDEO = "VIDEO"
    ARTICLE = "ARTICLE"
    BOOK = "BOOK"
    COURSE = "COURSE"
    DOCUMENT = "DOCUMENT"
    WEBSITE = "WEBSITE"
    PODCAST = "PODCAST"
    OTHER = "OTHER"


# ===========================================================================
# Topics
# ===========================================================================


class TopicCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)


class TopicUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)
    completed: bool | None = None


class TopicResponse(BaseModel):
    id: str
    moduleId: str
    title: str
    order: float
    content: str | None = None
    completed: bool = False
    estimatedHours: float | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class TopicGenerateRequest(BaseModel):
    """AI content generation type for a topic."""

    type: Literal["explain", "quiz", "summary", "flashcards"]


class TopicGenerateResponse(BaseModel):
    type: str
    topicId: str
    content: str


# ===========================================================================
# Modules
# ===========================================================================


class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    description: str | None = None


class ModuleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    description: str | None = None


class ModuleResponse(BaseModel):
    id: str
    courseId: str
    title: str
    order: float
    description: str | None = None
    completed: bool = False
    progress: float = 0.0
    topicCount: int = 0
    completedTopicCount: int = 0
    topics: list[TopicResponse] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Courses
# ===========================================================================


class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    circleId: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    archived: bool | None = None
    circleId: str | None = None


class CourseResponse(BaseModel):
    id: str
    userId: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    archived: bool = False
    progress: float = 0.0
    totalTopics: int = 0
    completedTopics: int = 0
    modules: list[ModuleResponse] = []
    createdAt: datetime
    updatedAt: datetime
    outlineSatisfactionRecorded: bool = False

    model_config = ConfigDict(from_attributes=True)


class CourseListItem(BaseModel):
    id: str
    userId: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    archived: bool = False
    progress: float = 0.0
    totalTopics: int = 0
    completedTopics: int = 0
    moduleCount: int = 0
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CourseListResponse(BaseModel):
    courses: list[CourseListItem]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class AICourseRequest(BaseModel):
    """AI-generated course request."""

    topic: str = Field(..., min_length=1, max_length=8000)
    difficulty: DifficultyLevel = DifficultyLevel.BEGINNER


class CourseOutlineSatisfactionCreate(BaseModel):
    """Learner feedback on AI-generated outline (KPI)."""

    kind: Literal["SATISFIED", "NOT_SATISFIED", "MODIFICATION_REQUESTED"]
    feedback: str | None = Field(None, max_length=4000)


# ===========================================================================
# Course Detail (rich view)
# ===========================================================================


class ContributionDay(BaseModel):
    date: str
    minutes: float


class CourseFootprint(BaseModel):
    last7DaysMinutes: float = 0.0
    last30DaysMinutes: float = 0.0
    daily: list[ContributionDay] = []


class StreakSummary(BaseModel):
    currentStreak: int = 0
    longestStreak: int = 0


class ModuleProgress(BaseModel):
    moduleId: str
    title: str
    order: float
    progress: float
    totalTopics: int
    completedTopics: int
    completed: bool


class ScheduleItem(BaseModel):
    id: str
    title: str
    startAt: str
    endAt: str
    courseId: str | None = None
    topicId: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CourseDetailResponse(BaseModel):
    course: CourseResponse
    userStreak: StreakSummary
    courseStreak: StreakSummary
    footprint: CourseFootprint
    schedules: list[ScheduleItem] = []
    completedTopics: int = 0
    totalModules: int = 0
    completedModules: int = 0
    totalEstimatedHours: float = 0.0
    completedEstimatedHours: float = 0.0
    modules: list[ModuleProgress] = []


# ===========================================================================
# Progress
# ===========================================================================


class ProgressResponse(BaseModel):
    courseId: str
    overallProgress: float
    totalTopics: int
    completedTopics: int = 0
    totalModules: int = 0
    completedModules: int = 0
    totalEstimatedHours: float = 0.0
    completedEstimatedHours: float = 0.0
    modules: list[ModuleProgress] = []


# ===========================================================================
# User Analytics (cross-course)
# ===========================================================================


class CourseProgressItem(BaseModel):
    courseId: str
    title: str
    progress: float
    totalTopics: int
    completedTopics: int
    totalModules: int
    completedModules: int
    isArchived: bool
    createdAt: str


class UserProgressSummary(BaseModel):
    userId: str
    totalCourses: int
    activeCourses: int
    completedCourses: int
    archivedCourses: int
    totalModules: int
    completedModules: int
    totalTopics: int
    completedTopics: int
    overallProgress: float
    totalEstimatedHours: float
    completedEstimatedHours: float
    averageCourseProgress: float


class UserAnalyticsResponse(BaseModel):
    summary: UserProgressSummary
    courses: list[CourseProgressItem]


# ===========================================================================
# Resources
# ===========================================================================


class ResourceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    url: str
    description: str | None = None
    type: ResourceType = ResourceType.OTHER
    metadata: dict | None = None
    isRecommended: bool = False
    recommendationScore: float | None = None
    recommendationSource: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    circleId: str | None = None


class ResourceResponse(BaseModel):
    id: str
    userId: str
    title: str
    url: str
    description: str | None = None
    type: str
    metadata: dict | None = None
    isRecommended: bool = False
    recommendationScore: float | None = None
    recommendationSource: str | None = None
    clickCount: int = 0
    bookmarkCount: int = 0
    circleId: str | None = None
    lastAccessedAt: str | None = None
    createdAt: str
    updatedAt: str


class ResourceListResponse(BaseModel):
    resources: list[ResourceResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class ResourceRecommendationRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(5, ge=1, le=20)
    context: dict | None = None


class ResourceRecommendationItem(BaseModel):
    title: str
    url: str
    description: str | None = None
    type: str = "OTHER"
    relevance: str | None = None
    score: float = 0.5


class ResourceRecommendationResponse(BaseModel):
    recommendations: list[ResourceRecommendationItem]
    query: str
    personalized: bool = True


# ===========================================================================
# Course Filters
# ===========================================================================


class CourseFilters(BaseModel):
    """Query parameters for filtering courses."""

    archived: bool | None = None
    difficulty: DifficultyLevel | None = None
    isAIGenerated: bool | None = None
    search: str | None = Field(None, max_length=255)
    page: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    sortBy: str = Field("createdAt", pattern="^(createdAt|updatedAt|title|progress)$")
    sortOrder: str = Field("desc", pattern="^(asc|desc)$")
    circleId: str | None = None
