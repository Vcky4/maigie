"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model for response schemas that read from SQLAlchemy ORM objects.

    Automatically converts snake_case Python attributes (from SQLAlchemy models)
    to camelCase JSON fields. Supports both:
    - Direct construction with camelCase kwargs: CamelModel(userId="abc")
    - ORM attribute reading: CamelModel.model_validate(orm_object)
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


ResponseT = TypeVar("ResponseT")


class PaginatedResponse(CamelModel, Generic[ResponseT]):
    """Canonical pagination envelope for personal-learning list endpoints."""

    items: list[ResponseT]
    total: int
    page: int
    page_size: int
    pages: int


# ===========================================================================
# Notes
# ===========================================================================


class NoteTagResponse(CamelModel):
    id: str
    tag: str


class NoteAttachmentCreate(BaseModel):
    filename: str
    url: str
    size: int | None = None


class NoteAttachmentResponse(CamelModel):
    id: str
    filename: str
    url: str
    size: int | None = None
    created_at: datetime | None = None


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool = False
    voiceRecordingUrl: str | None = None
    tags: list[str] | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool | None = None
    voiceRecordingUrl: str | None = None
    tags: list[str] | None = None


class NoteResponse(CamelModel):
    id: str
    user_id: str
    title: str
    content: str | None = None
    summary: str | None = None
    course_id: str | None = None
    topic_id: str | None = None
    archived: bool = False
    voice_recording_url: str | None = None
    tags: list[NoteTagResponse] = []
    attachments: list[NoteAttachmentResponse] = []
    created_at: datetime
    updated_at: datetime


class NoteImportRequest(CamelModel):
    """Import a personal note into a Learning Space."""

    spaceId: str


# ===========================================================================
# Exam Preparation
# ===========================================================================


# Removed: ExamPrepCreate, ExamPrepUpdate, ExamPrepMaterialResponse, MaterialUpdate,
# and TopicUpdate. None were referenced by any route or service. The live
# equivalents are PrepCreateRequest, PrepUpdateRequest, PrepMaterialResponse,
# PrepMaterialUpdateRequest, and PrepTopicUpdateRequest, further down this file.


class QuizStartRequest(CamelModel):
    mode: str = Field(
        ..., description="FULL_PRACTICE, WEAK_AREAS, TOPIC_FOCUS, PAST_PAPER_SIM, QUICK_REVIEW"
    )
    topic_id: str | None = None
    question_count: int | None = None


class AnswerSubmitRequest(CamelModel):
    question_id: str
    user_answer: str
    time_taken_seconds: int | None = None


class QuizCompleteRequest(CamelModel):
    duration_seconds: int | None = None


# ===========================================================================
# Document Generation
# ===========================================================================


class DocumentGenerateRequest(CamelModel):
    """Request to generate an academic document."""

    type: str = Field(..., description="essay, report, presentation, letter, cv")
    title: str = Field(..., min_length=1, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=5000)
    format: str = Field("pdf", description="pdf, docx, pptx")
    style: str = Field("academic", description="academic, report, minimal")
    courseId: str | None = None
    topicId: str | None = None


class DocumentResponse(CamelModel):
    id: str
    user_id: str
    title: str
    format: str | None = None
    style: str | None = None
    filename: str | None = None
    file_url: str | None = None
    preview_url: str | None = None
    share_id: str | None = None
    is_public: bool = False
    created_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    pageSize: int


# ===========================================================================
# Generic
# ===========================================================================


class MessageResponse(BaseModel):
    message: str


# ===========================================================================
# Home Response
# ===========================================================================


class TodaysFocusResponse(BaseModel):
    title: str | None = None  # Primary field for autonomous guidance
    courseTitle: str | None = None  # Keep for backward compat
    topicTitle: str | None = None  # Keep for backward compat
    reason: str
    estimatedMinutes: int | None = None
    type: str | None = None  # review_flashcards, complete_plan_item, study_topic, set_purpose, etc.
    actionData: dict | None = None  # Data needed to execute the action


class ProgressSummaryResponse(BaseModel):
    currentStreak: int
    activeDaysThisWeek: list[str] = Field(default_factory=list)
    cardsReviewedThisWeek: int
    cardsReviewedTotal: int
    cardsMastered: int
    totalCards: int
    dueCards: int
    consistencyScore: float | None = None
    averageSessionMinutes: float | None = None
    # Deprecated compatibility fields. They remain nullable/zero until a real
    # study-session and topic-completion source is introduced.
    weeklyMinutes: float | None = None
    topicsCompletedThisWeek: int = 0


class DueReviewResponse(BaseModel):
    id: str
    type: str  # "flashcard" | "topic"
    title: str
    dueAt: datetime
    urgency: int
    deckId: str | None = None
    deckTitle: str | None = None
    repetitionCount: int = 0
    intervalDays: int = 0
    lastReviewedAt: datetime | None = None


class ScheduleBlockResponse(BaseModel):
    id: str
    title: str
    startAt: datetime
    endAt: datetime
    type: str
    actionData: dict | None = None


class RecommendationResponse(BaseModel):
    type: str
    title: str
    reason: str
    actionData: dict | None = None


class NextActionResponse(BaseModel):
    type: str
    title: str
    actionData: dict | None = None


class ReEngagementResponse(BaseModel):
    message: str
    suggestedAction: NextActionResponse


class HomeResponse(BaseModel):
    greeting: str
    todaysFocus: TodaysFocusResponse | None = None
    progressSummary: ProgressSummaryResponse
    dueReviews: list[DueReviewResponse]
    scheduleBlocks: list[ScheduleBlockResponse]
    readyForYou: list[dict] = []  # Things prepared by the system
    stage: str = "active"  # fresh, purpose_set, setting_up, active
    nextAction: NextActionResponse | dict | None = None
    recommendations: list[RecommendationResponse] = []  # Deprecated — replaced by readyForYou
    reEngagement: ReEngagementResponse | None = None
    isOnboarding: bool  # Kept for backward compat
    # --- Commercial fields ---
    premiumSuggestion: dict | None = (
        None  # Conversion trigger (trigger, message, capability, upgradeUrl)
    )
    trialStatus: dict | None = None  # Trial status (isActive, dayNumber, daysRemaining)
    valueSummary: dict | None = None  # Value highlights (topAchievements, topFeaturesUsed)
    educatorPath: dict | None = None  # Educator transition (ready, message, actionUrl)
    milestone: dict | None = None  # New milestone achieved (milestoneId, title, shareText)


# ===========================================================================
# Flashcards
# ===========================================================================


class FlashcardCreate(BaseModel):
    front: str
    back: str
    deckId: str | None = None
    sourceType: str | None = None
    sourceId: str | None = None


class FlashcardResponse(CamelModel):
    id: str
    user_id: str
    deck_id: str | None = None
    front: str
    back: str
    interval_days: int
    repetition_count: int
    ease_factor: float
    # `nextReviewAt` and `lastQuality` are NOT NULL columns, so they are always
    # present. `lastQuality` uses -1 to mean "never reviewed".
    next_review_at: datetime
    last_reviewed_at: datetime | None = None
    last_quality: int
    lapse_count: int
    source_type: str | None = None
    source_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FlashcardReviewRequest(CamelModel):
    quality: int = Field(ge=0, le=5)


class FlashcardStats(BaseModel):
    total: int
    dueToday: int
    masteredCount: int
    averageEaseFactor: float


class DeckCreate(BaseModel):
    title: str
    description: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    prepId: str | None = None


class DeckResponse(CamelModel):
    id: str
    user_id: str
    title: str
    description: str | None = None
    course_id: str | None = None
    topic_id: str | None = None
    prep_id: str | None = None
    # Aggregated in a single grouped query by the deck listing.
    card_count: int
    due_count: int
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Saved Resources
# ===========================================================================


class SavedResourceCreate(BaseModel):
    title: str
    url: str | None = None
    sourceType: str
    sourceId: str | None = None
    tags: list[str] | None = None


class SavedResourceResponse(CamelModel):
    id: str
    user_id: str
    title: str
    url: str | None = None
    source_type: str
    source_id: str | None = None
    tags: list[str] | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime


class SavedResourceTagUpdate(BaseModel):
    tags: list[str]


# ===========================================================================
# Learning Profile
# ===========================================================================


LearningPurpose = Literal[
    "exam_prep",
    "skill_building",
    "course_completion",
    "professional_certification",
    "general_learning",
]
LlmProvider = Literal["gemini", "openai", "anthropic"]


class LearningProfileResponse(CamelModel):
    id: str
    user_id: str
    purpose: LearningPurpose | None = None
    subjects: list[str] | None = None
    goals_text: str | None = None
    preferred_explanation_style: str | None = None
    onboarding_completed_at: datetime | None = None
    maturity_days: int = 0
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    max_daily_notifications: int = 5
    preferred_llm_provider: LlmProvider | None = None
    created_at: datetime
    updated_at: datetime


class PurposeSetRequest(BaseModel):
    purpose: LearningPurpose


class LlmProviderSetRequest(BaseModel):
    provider: LlmProvider


class SubjectsSetRequest(CamelModel):
    subjects: list[str] = Field(default_factory=list)
    goals: str | None = None


# ===========================================================================
# Notifications
# ===========================================================================


class NotificationResponse(CamelModel):
    id: str
    user_id: str
    type: str
    title: str
    body: str | None = None
    priority: int | None = None
    action_data: dict | None = None
    scheduled_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    status: str
    created_at: datetime


# ===========================================================================
# Preparation Extensions
# ===========================================================================


PreparationType = Literal[
    "EXAM",
    "CERTIFICATION",
    "INTERVIEW",
    "PRESENTATION",
    "ASSIGNMENT",
    "PROJECT",
]

# The only values the service ever writes. `SETUP` on create, `IN_PROGRESS` once
# material is uploaded, `COMPLETED` on completion or when the target date passes.
PreparationStatus = Literal["SETUP", "IN_PROGRESS", "COMPLETED"]

PrepMaterialCategory = Literal[
    "TEXTBOOK",
    "NOTES",
    "PAST_QUESTION",
    "LINK",
    "SLIDE",
    "OTHER",
]


class PrepTopicResponse(CamelModel):
    id: str
    prep_id: str
    title: str
    description: str | None = None
    estimated_minutes: int | None = None
    order_index: int
    mastery_score: float | None = None
    status: str
    created_at: datetime


class PrepMaterialResponse(CamelModel):
    """Single material, including its extracted text.

    Use `PrepMaterialSummary` for listings: extracted text can be an entire
    chapter and must not be shipped once per row.
    """

    id: str
    prep_id: str
    filename: str
    url: str
    file_type: str | None = None
    size: int | None = None
    extracted_text: str | None = None
    category: str | None = None
    label: str | None = None
    created_at: datetime


class PrepMaterialSummary(CamelModel):
    """Listing shape. Deliberately omits `extractedText`."""

    id: str
    prep_id: str
    filename: str
    url: str
    file_type: str | None = None
    size: int | None = None
    category: str | None = None
    label: str | None = None
    # Whether extracted text is available, so the client can tell the learner
    # whether topic extraction has anything to work from.
    has_extracted_text: bool
    created_at: datetime


class PrepMaterialCreateRequest(CamelModel):
    """Replaces the previously untyped `body: dict`."""

    filename: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2000)
    file_type: str | None = Field(default=None, max_length=100)
    size: int | None = Field(default=None, ge=0)
    extracted_text: str | None = None
    category: PrepMaterialCategory = "OTHER"
    label: str | None = Field(default=None, max_length=200)


class PrepMaterialUpdateRequest(CamelModel):
    category: PrepMaterialCategory | None = None
    label: str | None = Field(default=None, max_length=200)


class PrepTopicUpdateRequest(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=600)
    order_index: int | None = Field(default=None, ge=0)
    mastery_score: float | None = Field(default=None, ge=0, le=100)
    status: str | None = None


class PrepCreateRequest(CamelModel):
    subject: str = Field(min_length=1, max_length=200)
    prep_type: PreparationType = Field(
        validation_alias="type",
        serialization_alias="type",
        description="What is being prepared for.",
    )
    target_date: str = Field(description="ISO-8601 target/exam date.")
    description: str | None = None


class PrepUpdateRequest(CamelModel):
    """Partial update. Only supplied fields are written."""

    subject: str | None = Field(default=None, min_length=1, max_length=200)
    prep_type: PreparationType | None = Field(
        default=None, validation_alias="type", serialization_alias="type"
    )
    target_date: str | None = None
    description: str | None = None
    status: PreparationStatus | None = None


class PrepSummaryResponse(CamelModel):
    """`examDate` is a NOT NULL column, so it is always present."""

    id: str
    user_id: str
    subject: str
    prep_type: PreparationType | None = Field(
        default=None, validation_alias="prep_type", serialization_alias="type"
    )
    exam_date: datetime
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Quiz
# ===========================================================================


class QuizQuestionResponse(CamelModel):
    id: str
    question_text: str
    question_type: str
    options: list[str] | None = None
    order_index: int
    prep_topic_id: str | None = None
    correct_answer: str
    explanation: str | None = None
    # User's answer (present if they've answered this question)
    user_answer: str | None = None
    is_correct: bool | None = None
    time_taken_seconds: int | None = None
    answered_at: datetime | None = None


class QuizSessionResponse(CamelModel):
    """`correctCount` is a NOT NULL column defaulting to 0, so it is always present."""

    id: str
    user_id: str
    prep_id: str
    mode: str
    topic_id: str | None = None
    status: str
    total_questions: int
    correct_count: int
    score_percentage: float | None = None
    duration_seconds: int | None = None
    completed_at: datetime | None = None
    created_at: datetime
    questions: list[QuizQuestionResponse] = []


class AnswerResultResponse(CamelModel):
    question_id: str
    is_correct: bool
    correct_answer: str
    explanation: str | None = None


class QuizTopicBreakdown(CamelModel):
    """Mirrors what `quiz_engine._compute_topic_breakdown` emits."""

    topic_id: str
    title: str
    total: int
    correct: int
    score: float


class QuizSummaryResponse(CamelModel):
    quiz_id: str
    total_questions: int
    correct_count: int
    score_percentage: float
    topic_breakdown: list[QuizTopicBreakdown]
    weak_areas: list[str]
    suggested_next_step: str | None = None


# ===========================================================================
# Study Plans
# ===========================================================================


class StudyPlanCreate(BaseModel):
    title: str
    goalDescription: str | None = None
    deadline: str
    prepId: str | None = None


class StudyPlanItemResponse(CamelModel):
    """Mirrors the persisted item; only genuinely nullable columns are optional."""

    id: str
    plan_id: str
    title: str
    description: str | None = None
    scheduled_date: datetime
    estimated_minutes: int
    item_type: str
    topic_id: str | None = None
    prep_topic_id: str | None = None
    status: str
    completed_at: datetime | None = None


class StudyPlanResponse(CamelModel):
    """Mirrors the persisted plan.

    `deadline`, `totalItems`, and `completedItems` are NOT NULL columns, and
    plan queries eagerly load `items`, so these are always serialized.
    """

    id: str
    user_id: str
    title: str
    goal_description: str | None = None
    deadline: datetime
    prep_id: str | None = None
    status: str
    total_items: int
    completed_items: int
    items: list[StudyPlanItemResponse]
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Reflections
# ===========================================================================


class ReflectionResponse(CamelModel):
    id: str
    user_id: str
    type: str
    period_start: datetime
    period_end: datetime
    summary: str
    activities_layer: dict | None = None
    progress_layer: dict | None = None
    achievements_layer: dict | None = None
    recommendations: list[str] | dict | None = None
    created_at: datetime


class ReflectionGenerateRequest(CamelModel):
    type: str = Field(description="weekly or monthly")


# ===========================================================================
# Discovery
# ===========================================================================


class DiscoveryRecommendationResponse(CamelModel):
    id: str
    user_id: str
    item_type: str
    item_id: str
    title: str
    reason: str
    relevance_score: float
    status: str
    created_at: datetime


# ===========================================================================
# Activity Feed
# ===========================================================================


class ActivityFeedEntryResponse(CamelModel):
    id: str
    user_id: str
    activity_type: str
    title: str
    description: str | None = None
    context: dict | None = None
    occurred_at: datetime


class ActivityFeedResponse(BaseModel):
    items: list[ActivityFeedEntryResponse]
    total: int
    page: int
    pageSize: int


# ===========================================================================
# Course Study
# ===========================================================================


class CourseProgressResponse(BaseModel):
    courseId: str
    title: str
    progress: float
    totalTopics: int
    completedTopics: int
    currentTopicId: str | None = None
    currentTopicTitle: str | None = None


class LearningPathTopicResponse(BaseModel):
    topicId: str
    title: str
    moduleTitle: str
    status: str
    estimatedMinutes: int
    order: int


class LearningPathResponse(BaseModel):
    courseId: str
    courseTitle: str
    topics: list[LearningPathTopicResponse]
    completionPercentage: float
    estimatedMinutesRemaining: int


# ===========================================================================
# Behaviour
# ===========================================================================


class BehaviourProfileResponse(BaseModel):
    preferredTimes: dict | None = None
    avgSessionMinutes: float | None = None
    consistencyScore: float | None = None
    bestDayOfWeek: str | None = None
    dropoutRiskFactors: list[str] | None = None


# ===========================================================================
# Learn Dashboard
# ===========================================================================


LearnDashboardSection = Literal[
    "featured",
    "review",
    "stats",
    "courses",
    "paths",
    "tools",
    "recentItems",
    "collections",
]
LearnEntityType = Literal[
    "course",
    "topic",
    "note",
    "saved_resource",
    "document",
    "study_plan",
    "preparation",
]


class LearnDashboardMeta(CamelModel):
    generated_at: datetime
    degraded_sections: list[LearnDashboardSection]


class LearnFeaturedItem(CamelModel):
    entity_type: Literal["course", "topic"]
    entity_id: str
    course_id: str
    topic_id: str | None
    title: str
    description: str | None
    course_title: str
    estimated_minutes: int | None
    progress_percent: float
    completed_units: int
    total_units: int


class LearnReviewSummary(CamelModel):
    due_cards: int
    overdue_cards: int
    estimated_minutes: int
    mastery_percent: float | None


class LearnDashboardStats(CamelModel):
    active_courses: int
    completed_topics: int
    saved_resources: int
    personal_notes: int
    generated_documents: int


class LearnNextTopic(CamelModel):
    id: str
    title: str
    estimated_minutes: int | None


class LearnCourseSummary(CamelModel):
    id: str
    title: str
    description: str | None
    difficulty: str | None
    progress_percent: float
    completed_topics: int
    total_topics: int
    module_count: int
    next_topic: LearnNextTopic | None
    updated_at: datetime


class LearnCourseList(CamelModel):
    items: list[LearnCourseSummary]
    total: int


class LearnPathSummary(CamelModel):
    entity_type: Literal["study_plan", "preparation"]
    id: str
    title: str
    description: str | None
    status: str
    deadline: datetime | None
    completed_units: int
    total_units: int
    progress_percent: float


class LearnToolSummary(CamelModel):
    type: Literal[
        "course",
        "note",
        "flashcard",
        "saved_resource",
        "document",
        "study_plan",
    ]
    count: int


class LearnRecentItem(CamelModel):
    entity_type: Literal["note", "saved_resource", "document"]
    id: str
    title: str
    context_label: str | None
    occurred_at: datetime


class LearnCollectionSummary(CamelModel):
    id: str
    title: str
    description: str | None
    item_count: int
    source: Literal["course", "topic", "tag"]


class LearnDashboardResponse(CamelModel):
    """Every field is always serialized.

    Fields are required rather than defaulted so the published OpenAPI contract
    matches what the service actually returns; nullable fields stay nullable.
    """

    meta: LearnDashboardMeta
    featured: LearnFeaturedItem | None
    review: LearnReviewSummary
    stats: LearnDashboardStats
    courses: LearnCourseList
    paths: list[LearnPathSummary]
    tools: list[LearnToolSummary]
    recent_items: list[LearnRecentItem]
    collections: list[LearnCollectionSummary]
