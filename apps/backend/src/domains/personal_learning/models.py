"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import date, datetime
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


MasteryBand = Literal["focus", "review", "strong"]

PrepareDashboardSection = Literal[
    "summary",
    "preparations",
    "focusTopics",
    "recentSessions",
]


class PrepareDashboardMeta(CamelModel):
    generated_at: datetime
    degraded_sections: list[PrepareDashboardSection]


class PrepareSummaryStats(CamelModel):
    """Totals across the learner's active preparations."""

    active_preparations: int
    questions_answered: int
    # None until at least one question has been answered.
    accuracy_percent: float | None
    # Derived from recorded session durations, which are nullable, so this is
    # tracked practice time rather than total time spent.
    practice_minutes: int
    quizzes_taken: int
    # Consecutive days with at least one completed quiz session (Decision I).
    # None when the learner has never completed one; 0 once a streak has lapsed.
    # A deliberately secondary signal — do not render it above readiness.
    practice_streak: int | None


class PreparationProgressSummary(CamelModel):
    """A preparation with its derived progress.

    `progressPercent` is `topicsStrong / topicsTotal` and is the same number the
    Learn dashboard shows for this preparation. `averageMasteryPercent` is the
    mean topic mastery: a better readiness signal, but not a progress ratio, so
    it must not be rendered next to the unit counts as though it were one.
    """

    id: str
    subject: str
    description: str | None
    status: str
    prep_type: PreparationType | None = Field(
        default=None, validation_alias="prep_type", serialization_alias="type"
    )
    exam_date: datetime
    # None once the target date has passed.
    days_until_exam: int | None
    progress_percent: float
    average_mastery_percent: float | None
    topics_total: int
    topics_strong: int
    topics_focus: int
    # How many topics have actually been practised, so a surface can qualify the
    # numbers above instead of implying an unpractised preparation is hopeless.
    topics_assessed: int
    questions_answered: int
    accuracy_percent: float | None
    quizzes_taken: int
    # False until topics exist; practice cannot start before then.
    practice_ready: bool


class PrepareFocusTopic(CamelModel):
    """A topic worth practising next, weakest first."""

    id: str
    preparation_id: str
    preparation_subject: str
    title: str
    mastery_percent: float
    band: MasteryBand
    order_index: int


class PrepareSessionSummary(CamelModel):
    id: str
    preparation_id: str
    preparation_subject: str
    mode: str
    status: str
    total_questions: int
    correct_count: int
    score_percent: float | None
    duration_seconds: int | None
    completed_at: datetime | None
    created_at: datetime


class PrepareDashboardResponse(CamelModel):
    meta: PrepareDashboardMeta
    summary: PrepareSummaryStats
    preparations: list[PreparationProgressSummary]
    preparations_total: int
    focus_topics: list[PrepareFocusTopic]
    recent_sessions: list[PrepareSessionSummary]


PreparationConfidence = Literal["STARTING", "DEVELOPING", "CONFIDENT"]
PreparationPace = Literal["LIGHT", "BALANCED", "INTENSIVE"]


class PrepReadinessPoint(CamelModel):
    """One day of a preparation's readiness.

    Nullable percentages preserve the not-measured-versus-zero rule: a day on which
    a preparation had no topics has no measurable readiness, and a chart should skip
    the point rather than plot it at zero.
    """

    captured_on: date
    progress_percent: float
    average_mastery_percent: float | None = None
    topics_total: int
    topics_strong: int
    topics_focus: int
    topics_assessed: int
    questions_answered: int
    accuracy_percent: float | None = None
    quizzes_taken: int


class PrepReadinessTrendResponse(CamelModel):
    """A preparation's readiness over a bounded window.

    `points` contains **only days that were actually captured.** A preparation
    created today has an empty series, and the client must say so rather than
    drawing a line through a single point or back-filling from today's value —
    either would fabricate the history the chart claims to show.
    """

    preparation_id: str
    days: int
    points: list[PrepReadinessPoint]


class PrepTimelineMilestone(CamelModel):
    """One point on a preparation's timeline.

    Derived from a study-plan item, except the final `EXAM` entry which comes from
    the preparation's own target date.
    """

    id: str
    kind: Literal["STUDY", "EXAM"]
    title: str
    detail: str | None = None
    scheduled_for: datetime
    estimated_minutes: int | None = None
    status: str
    item_type: str | None = None
    prep_topic_id: str | None = None
    study_plan_id: str | None = None
    completed_at: datetime | None = None


class PrepTimelineResponse(CamelModel):
    """A preparation's timeline, derived rather than stored.

    `hasStudyPlan` is `False` until a plan has been generated, in which case
    `milestones` contains only the exam. The client should offer plan generation
    rather than rendering that as a planned-and-empty timeline.
    """

    preparation_id: str
    has_study_plan: bool
    milestones: list[PrepTimelineMilestone]


class PrepCreateRequest(CamelModel):
    subject: str = Field(min_length=1, max_length=200)
    prep_type: PreparationType = Field(
        validation_alias="type",
        serialization_alias="type",
        description="What is being prepared for.",
    )
    target_date: str = Field(description="ISO-8601 target/exam date.")
    description: str | None = None
    # Collected by the create wizard. Optional because a preparation is perfectly
    # valid without them; they shape scheduling, they are not prerequisites.
    confidence: PreparationConfidence | None = Field(
        default=None, description="The learner's self-reported starting point."
    )
    pace: PreparationPace | None = Field(
        default=None,
        description=(
            "How hard the learner wants to push. Determines the study-plan effort "
            "budget: LIGHT ~3 sessions/week, BALANCED ~5, INTENSIVE daily."
        ),
    )


class PrepUpdateRequest(CamelModel):
    """Partial update. Only supplied fields are written."""

    subject: str | None = Field(default=None, min_length=1, max_length=200)
    prep_type: PreparationType | None = Field(
        default=None, validation_alias="type", serialization_alias="type"
    )
    target_date: str | None = None
    description: str | None = None
    status: PreparationStatus | None = None
    confidence: PreparationConfidence | None = None
    pace: PreparationPace | None = None


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
    # Plain strings on the way out, tolerant of legacy values, matching how
    # `status` is handled: strict on write, tolerant on read.
    confidence: str | None = None
    pace: str | None = None
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Entitlement
# ===========================================================================


class UpgradeRequiredDetail(CamelModel):
    """Body of a `403` raised by a tier gate.

    Previously an ad-hoc dict, so the client had nothing to render an upgrade
    path from. Built from `feature_tier_service.CapabilityDenied`.
    """

    # Required rather than defaulted, so it is a dependable discriminant in the
    # generated client types instead of an optional flag.
    upgrade_required: Literal[True]
    reason: str
    capability: str
    upgrade_url: str
    trial_available: bool
    upgrade_value: str


class UpgradeRequiredResponse(CamelModel):
    """`403` envelope. FastAPI nests `HTTPException.detail` under `detail`."""

    detail: UpgradeRequiredDetail


# ===========================================================================
# Quiz
# ===========================================================================


class QuizQuestionPresentation(CamelModel):
    """A question as delivered to the learner.

    `correctAnswer` and `explanation` are populated **as soon as this question
    has been answered**, so the learner gets the explanation while they practise
    and keeps it if they navigate back or resume the session. They are withheld
    for questions not yet attempted (Decision C). A completed session reveals
    every question, including any left unanswered.

    That makes both fields nullable on the wire even though `correctAnswer` is a
    NOT NULL column: the nullability describes what has been disclosed so far,
    not what is stored.
    """

    id: str
    question_text: str
    question_type: str
    options: list[str] | None = None
    order_index: int
    prep_topic_id: str | None = None
    # Safe to show before answering: metadata about the question, not about its
    # answer. `None` for questions banked before difficulty was recorded.
    difficulty: str | None = None
    # Disclosed with the answer key, not before. A tip written about a specific
    # question can hint at its answer, so it sits on the key's side of the
    # boundary rather than being treated as neutral metadata.
    exam_tip: str | None = None
    # Review-only. `None` unless the learner has answered this question, or the
    # session is COMPLETED.
    correct_answer: str | None = None
    explanation: str | None = None
    # The learner's own answer, present once they have answered this question.
    user_answer: str | None = None
    is_correct: bool | None = None
    time_taken_seconds: int | None = None
    answered_at: datetime | None = None


class QuizSessionResponse(CamelModel):
    """`correctCount` is a NOT NULL column defaulting to 0, so it is always present.

    `correctCount` and `scorePercentage` are derived from persisted answers by
    the server; a client cannot influence them beyond submitting answers.
    """

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
    questions: list[QuizQuestionPresentation] = []


QuestionDifficulty = Literal["EASY", "MEDIUM", "HARD"]
QuestionSource = Literal["AI_GENERATED", "PAST_PAPER"]


class PrepQuestionBankItem(CamelModel):
    """A banked question as shown in the workspace question bank.

    **Deliberately omits `correctAnswer` and `explanation`.** The bank is a
    browsing surface, so serving the answer key here would reopen exactly the leak
    Decision C closed: a learner could read every answer before practising simply
    by opening the tab. Answers are disclosed by answering, or on review of a
    completed session.
    """

    id: str
    prep_id: str
    prep_topic_id: str | None = None
    question_text: str
    question_type: str
    options: list[str] | None = None
    # `EASY | MEDIUM | HARD`, or None for questions banked before this was recorded.
    difficulty: str | None = None
    # `AI_GENERATED | PAST_PAPER`. Server-set, so it can be trusted.
    source: str | None = None
    # Only meaningful for past papers.
    source_year: int | None = None
    # `examTip` is deliberately absent, for the same reason as the answer key: a
    # tip about a specific question can hint at its answer, and browsing must not
    # be a way to get either.
    # Lifetime statistics across every session that asked this question. Only
    # expressible now that a question outlives a session.
    times_answered: int
    times_correct: int
    # None until the question has been answered at least once, keeping
    # "not attempted" distinguishable from "always got it wrong".
    accuracy_percent: float | None = None
    # This learner's own flag. Scoped per user, so one learner never sees another's.
    is_flagged: bool = False
    flag_note: str | None = None
    created_at: datetime


class PrepQuestionFlagRequest(CamelModel):
    """Optional note explaining why the question was flagged.

    Optional on purpose: the act of flagging is the signal, and demanding a reason
    would suppress it.
    """

    note: str | None = Field(default=None, max_length=1000)


class PrepQuestionFlagResponse(CamelModel):
    question_id: str
    is_flagged: bool
    note: str | None = None
    created_at: datetime


class AnswerResultResponse(CamelModel):
    """Result for the one question just submitted, including its key.

    This is the only place an answer key is disclosed before the session
    completes, and only for a question the learner has now answered.
    """

    question_id: str
    is_correct: bool
    correct_answer: str
    explanation: str | None = None
    # True when this question had already been answered and the stored result
    # was replayed. Resubmitting never re-scores, so a retry is safe and the
    # key returned here cannot be used to raise the score.
    already_answered: bool = False


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
