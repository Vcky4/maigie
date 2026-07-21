"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import datetime
from typing import Any

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


class NoteListResponse(BaseModel):
    items: list[NoteResponse]
    total: int
    page: int
    size: int
    pages: int


class NoteImportRequest(CamelModel):
    """Import a personal note into a Learning Space."""

    spaceId: str


# ===========================================================================
# Exam Preparation
# ===========================================================================


class ExamPrepCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    exam_date: str = Field(..., description="ISO date string (e.g. 2025-03-15)")
    description: str | None = None


class ExamPrepUpdate(BaseModel):
    subject: str | None = None
    exam_date: str | None = None
    description: str | None = None
    status: str | None = None


class ExamPrepMaterialResponse(BaseModel):
    id: str
    filename: str
    url: str
    extractedText: str | None = None
    fileType: str | None = None
    size: int | None = None
    category: str = "OTHER"
    label: str | None = None
    createdAt: str


class MaterialUpdate(BaseModel):
    category: str | None = None
    label: str | None = None


class TopicUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


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
    weeklyMinutes: float
    topicsCompletedThisWeek: int


class DueReviewResponse(BaseModel):
    id: str
    type: str  # "flashcard" | "topic"
    title: str
    dueAt: datetime
    urgency: int


class ScheduleBlockResponse(BaseModel):
    id: str
    title: str
    startAt: datetime
    endAt: datetime
    type: str


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
    next_review_at: datetime | None = None
    last_reviewed_at: datetime | None = None
    last_quality: int | None = None
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
    created_at: datetime


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


class LearningProfileResponse(CamelModel):
    id: str
    user_id: str
    purpose: str | None = None
    subjects: list | None = None
    goals_text: str | None = None
    preferred_explanation_style: str | None = None
    onboarding_completed_at: datetime | None = None
    maturity_days: int = 0
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    preferred_study_times: dict | None = None
    avg_session_minutes: float | None = None
    consistency_score: float | None = None
    best_day_of_week: str | None = None
    dropout_risk: float | None = None
    preferred_llm_provider: str | None = None
    created_at: datetime | None = None


class PurposeSetRequest(BaseModel):
    purpose: str = Field(
        description="exam_prep, skill_building, course_completion, professional_certification, general_learning"
    )


class LlmProviderSetRequest(BaseModel):
    provider: str = Field(
        description="gemini, openai, or anthropic"
    )


class SubjectsSetRequest(CamelModel):
    subjects: list[str] | None = None
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


class PrepCreateRequest(CamelModel):
    subject: str
    type: str = Field(
        description="EXAM, CERTIFICATION, INTERVIEW, PRESENTATION, ASSIGNMENT, PROJECT"
    )
    targetDate: str
    description: str | None = None


class PrepSummaryResponse(CamelModel):
    id: str
    user_id: str
    subject: str
    exam_date: datetime | None = None
    description: str | None = None
    status: str
    created_at: datetime


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
    id: str
    user_id: str
    prep_id: str
    mode: str
    topic_id: str | None = None
    status: str
    total_questions: int
    correct_count: int | None = None
    score_percentage: float | None = None
    duration_seconds: int | None = None
    completed_at: datetime | None = None
    created_at: datetime
    questions: list[QuizQuestionResponse] = []


class AnswerResultResponse(BaseModel):
    questionId: str
    isCorrect: bool
    correctAnswer: str
    explanation: str | None = None


class QuizSummaryResponse(BaseModel):
    quizId: str
    totalQuestions: int
    correctCount: int
    scorePercentage: float
    topicBreakdown: list[dict]
    weakAreas: list[str]
    suggestedNextStep: str | None = None


# ===========================================================================
# Study Plans
# ===========================================================================


class StudyPlanCreate(BaseModel):
    title: str
    goalDescription: str | None = None
    deadline: str
    prepId: str | None = None


class StudyPlanItemResponse(CamelModel):
    id: str
    plan_id: str
    title: str
    description: str | None = None
    scheduled_date: datetime | None = None
    estimated_minutes: int | None = None
    item_type: str | None = None
    topic_id: str | None = None
    prep_topic_id: str | None = None
    status: str
    completed_at: datetime | None = None


class StudyPlanResponse(CamelModel):
    id: str
    user_id: str
    title: str
    goal_description: str | None = None
    deadline: datetime | None = None
    prep_id: str | None = None
    status: str
    total_items: int | None = None
    completed_items: int | None = None
    items: list[StudyPlanItemResponse] | None = None
    created_at: datetime


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
