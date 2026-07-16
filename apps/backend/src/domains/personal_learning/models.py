"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Notes
# ===========================================================================


class NoteTagResponse(BaseModel):
    id: str
    tag: str

    model_config = ConfigDict(from_attributes=True)


class NoteAttachmentCreate(BaseModel):
    filename: str
    url: str
    size: int | None = None


class NoteAttachmentResponse(BaseModel):
    id: str
    filename: str
    url: str
    size: int | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


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


class NoteResponse(BaseModel):
    id: str
    userId: str
    title: str
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool = False
    voiceRecordingUrl: str | None = None
    tags: list[NoteTagResponse] = []
    attachments: list[NoteAttachmentResponse] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class NoteListResponse(BaseModel):
    items: list[NoteResponse]
    total: int
    page: int
    size: int
    pages: int


class NoteImportRequest(BaseModel):
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


class QuizStartRequest(BaseModel):
    mode: str = Field(
        ..., description="FULL_PRACTICE, WEAK_AREAS, TOPIC_FOCUS, PAST_PAPER_SIM, QUICK_REVIEW"
    )
    topic_id: str | None = None
    question_count: int | None = None


class AnswerSubmitRequest(BaseModel):
    question_id: str
    user_answer: str
    time_taken_seconds: int | None = None


class QuizCompleteRequest(BaseModel):
    duration_seconds: int | None = None


# ===========================================================================
# Document Generation
# ===========================================================================


class DocumentGenerateRequest(BaseModel):
    """Request to generate an academic document."""

    type: str = Field(..., description="essay, report, presentation, letter, cv")
    title: str = Field(..., min_length=1, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=5000)
    format: str = Field("pdf", description="pdf, docx, pptx")
    courseId: str | None = None
    topicId: str | None = None


class DocumentResponse(BaseModel):
    id: str
    userId: str
    title: str
    type: str
    format: str
    status: str
    downloadUrl: str | None = None
    previewUrl: str | None = None
    shareId: str | None = None
    isPublic: bool = False
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


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
    courseTitle: str | None = None
    topicTitle: str | None = None
    reason: str


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
    recommendations: list[RecommendationResponse]
    nextAction: NextActionResponse
    reEngagement: ReEngagementResponse | None = None
    isOnboarding: bool


# ===========================================================================
# Flashcards
# ===========================================================================


class FlashcardCreate(BaseModel):
    front: str
    back: str
    deckId: str | None = None
    sourceType: str | None = None
    sourceId: str | None = None


class FlashcardResponse(BaseModel):
    id: str
    userId: str
    deckId: str | None = None
    front: str
    back: str
    intervalDays: int
    repetitionCount: int
    easeFactor: float
    nextReviewAt: datetime | None = None
    lastReviewedAt: datetime | None = None
    lastQuality: int | None = None
    lapseCount: int
    sourceType: str | None = None
    sourceId: str | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class FlashcardReviewRequest(BaseModel):
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


class DeckResponse(BaseModel):
    id: str
    userId: str
    title: str
    description: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    prepId: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Saved Resources
# ===========================================================================


class SavedResourceCreate(BaseModel):
    title: str
    url: str | None = None
    sourceType: str
    sourceId: str | None = None
    tags: list[str] | None = None


class SavedResourceResponse(BaseModel):
    id: str
    userId: str
    title: str
    url: str | None = None
    sourceType: str
    sourceId: str | None = None
    tags: list[str] | None = None
    lastAccessedAt: datetime | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class SavedResourceTagUpdate(BaseModel):
    tags: list[str]


# ===========================================================================
# Learning Profile
# ===========================================================================


class LearningProfileResponse(BaseModel):
    id: str
    userId: str
    purpose: str | None = None
    subjects: list[str] | None = None
    goalsText: str | None = None
    preferredExplanationStyle: str | None = None
    onboardingCompletedAt: datetime | None = None
    maturityDays: int
    quietHoursStart: str | None = None
    quietHoursEnd: str | None = None
    preferredStudyTimes: list[str] | None = None
    avgSessionMinutes: float | None = None
    consistencyScore: float | None = None
    bestDayOfWeek: str | None = None
    dropoutRisk: float | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class PurposeSetRequest(BaseModel):
    purpose: str = Field(
        description="exam_prep, skill_building, course_completion, professional_certification, general_learning"
    )


class SubjectsSetRequest(BaseModel):
    subjects: list[str] | None = None
    goals: str | None = None


# ===========================================================================
# Notifications
# ===========================================================================


class NotificationResponse(BaseModel):
    id: str
    userId: str
    type: str
    title: str
    body: str | None = None
    priority: str | None = None
    actionData: dict | None = None
    scheduledAt: datetime | None = None
    deliveredAt: datetime | None = None
    readAt: datetime | None = None
    dismissedAt: datetime | None = None
    status: str
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Preparation Extensions
# ===========================================================================


class PrepTopicResponse(BaseModel):
    id: str
    prepId: str
    title: str
    description: str | None = None
    estimatedMinutes: int | None = None
    orderIndex: int
    masteryScore: float | None = None
    status: str
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class PrepMaterialResponse(BaseModel):
    id: str
    prepId: str
    filename: str
    url: str
    fileType: str | None = None
    size: int | None = None
    extractedText: str | None = None
    category: str | None = None
    label: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class PrepCreateRequest(BaseModel):
    subject: str
    type: str = Field(
        description="EXAM, CERTIFICATION, INTERVIEW, PRESENTATION, ASSIGNMENT, PROJECT"
    )
    targetDate: str
    description: str | None = None


class PrepSummaryResponse(BaseModel):
    id: str
    userId: str
    subject: str
    type: str
    examDate: str | None = None
    description: str | None = None
    status: str
    progressPercentage: float
    daysRemaining: int
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Quiz
# ===========================================================================


class QuizQuestionResponse(BaseModel):
    id: str
    questionText: str
    questionType: str
    options: list[str] | None = None
    orderIndex: int


class QuizSessionResponse(BaseModel):
    id: str
    userId: str
    prepId: str
    mode: str
    status: str
    totalQuestions: int
    correctCount: int | None = None
    scorePercentage: float | None = None
    durationSeconds: int | None = None
    completedAt: datetime | None = None
    questions: list[QuizQuestionResponse] | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


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


class StudyPlanItemResponse(BaseModel):
    id: str
    planId: str
    title: str
    description: str | None = None
    scheduledDate: str | None = None
    estimatedMinutes: int | None = None
    itemType: str | None = None
    topicId: str | None = None
    prepTopicId: str | None = None
    status: str
    completedAt: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class StudyPlanResponse(BaseModel):
    id: str
    userId: str
    title: str
    goalDescription: str | None = None
    deadline: str
    prepId: str | None = None
    status: str
    totalItems: int
    completedItems: int
    completionPercentage: float
    daysRemaining: int
    todaysTasks: list[StudyPlanItemResponse]
    items: list[StudyPlanItemResponse] | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Reflections
# ===========================================================================


class ReflectionResponse(BaseModel):
    id: str
    userId: str
    type: str
    periodStart: datetime
    periodEnd: datetime
    summary: str
    activitiesLayer: dict | None = None
    progressLayer: dict | None = None
    achievementsLayer: dict | None = None
    recommendations: dict | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class ReflectionGenerateRequest(BaseModel):
    type: str = Field(description="weekly or monthly")


# ===========================================================================
# Discovery
# ===========================================================================


class DiscoveryRecommendationResponse(BaseModel):
    id: str
    userId: str
    itemType: str
    itemId: str
    title: str
    reason: str
    relevanceScore: float
    status: str
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Activity Feed
# ===========================================================================


class ActivityFeedEntryResponse(BaseModel):
    id: str
    userId: str
    activityType: str
    title: str
    description: str | None = None
    context: dict | None = None
    occurredAt: datetime

    model_config = ConfigDict(from_attributes=True)


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
