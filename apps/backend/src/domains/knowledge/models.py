"""
Knowledge domain — Pydantic request/response schemas.

Covers courses, modules, topics, resources, resource bank,
AI generation, and progress analytics.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.shared.schemas import CamelModel, PaginatedResponse

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


class TopicResponse(CamelModel):
    """A topic as returned to the client.

    Fields are snake_case and the wire format stays camelCase, which is not cosmetic here: this model
    is validated straight off a SQLAlchemy `Topic`, and while the fields were declared camelCase that
    validation could not find `module_id`, `created_at` or `updated_at` at all — `POST .../topics`
    answered `500`. `estimatedHours` was worse: it has a default, so it silently came back null
    rather than failing, and the endpoint looked like it worked.
    """

    id: str
    module_id: str
    title: str
    order: float
    content: str | None = None
    completed: bool = False
    estimated_hours: float | None = None
    created_at: datetime
    updated_at: datetime


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


class ModuleResponse(CamelModel):
    """A module with its topics and derived progress.

    Built from `course_service.calculate_module_progress`, whose keys are camelCase and reach these
    snake_case fields as aliases. `topics` holds raw ORM rows, validated through `TopicResponse`.
    """

    id: str
    course_id: str
    title: str
    order: float
    description: str | None = None
    completed: bool = False
    progress: float = 0.0
    topic_count: int = 0
    completed_topic_count: int = 0
    topics: list[TopicResponse] = []
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Courses
# ===========================================================================


class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    spaceId: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    archived: bool | None = None
    spaceId: str | None = None


class CourseResponse(CamelModel):
    """A course with its modules, topics and derived progress.

    `progress`, `totalTopics` and `completedTopics` are computed per request from the topics rather
    than read from `Course.progress`, which nothing writes — see the note on that column.
    """

    id: str
    user_id: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    target_date: datetime | None = None
    # Explicit alias: the generator would produce `isAiGenerated`, and the published contract
    # — and every client reading it — says `isAIGenerated`. An acronym is where a camelCase
    # generator and a hand-named field disagree, so this one is pinned rather than derived.
    is_ai_generated: bool = Field(default=False, alias="isAIGenerated")
    archived: bool = False
    progress: float = 0.0
    total_topics: int = 0
    completed_topics: int = 0
    modules: list[ModuleResponse] = []
    created_at: datetime
    updated_at: datetime
    outline_satisfaction_recorded: bool = False


class CourseListItem(CamelModel):
    """A course as it appears in the library, without its modules or topics.

    `moduleCount` rather than the modules themselves: a library card shows a count, and the client
    type comment already records that this is not named `totalModules`.
    """

    id: str
    user_id: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    target_date: datetime | None = None
    # Explicit alias: the generator would produce `isAiGenerated`, and the published contract
    # — and every client reading it — says `isAIGenerated`. An acronym is where a camelCase
    # generator and a hand-named field disagree, so this one is pinned rather than derived.
    is_ai_generated: bool = Field(default=False, alias="isAIGenerated")
    archived: bool = False
    progress: float = 0.0
    total_topics: int = 0
    completed_topics: int = 0
    module_count: int = 0
    created_at: datetime
    updated_at: datetime


class CourseListResponse(PaginatedResponse[CourseListItem]):
    """The course library, one page at a time.

    Migrated onto the shared envelope: `items` rather than `courses`, and `pages` rather than
    `hasMore`. Done now because the only consumer is being rewritten from scratch in the same change,
    which is the cheapest this migration will ever be — every later moment costs a client rewrite as
    well.

    `pages` replaces `hasMore` because it answers strictly more: a pager needs to know how many pages
    there are, and "is there another one" is `page < pages`. `hasMore` could not be derived the other
    way round.
    """


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
    spaceId: str | None = None


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
    spaceId: str | None = None
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
    spaceId: str | None = None
