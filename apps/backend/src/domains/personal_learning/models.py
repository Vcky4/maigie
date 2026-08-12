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


# Every mode `quiz_engine.start_quiz` branches on. Constrained on the way in so an
# unknown mode is a 422 rather than silently falling through to the quick-review
# path — which is how `ADAPTIVE` came to be billed as a Plus feature while behaving
# exactly like the free one.
QuizMode = Literal[
    "FULL_PRACTICE",
    "QUICK_REVIEW",
    "WEAK_AREAS",
    "TOPIC_FOCUS",
    # Plus-only, gated by `feature_tier_service`.
    "PAST_PAPER_SIM",
    "ADAPTIVE",
]


class QuizStartRequest(CamelModel):
    mode: QuizMode = Field(
        ...,
        description=(
            "FULL_PRACTICE, QUICK_REVIEW, WEAK_AREAS, TOPIC_FOCUS, and the Plus-only "
            "PAST_PAPER_SIM and ADAPTIVE."
        ),
    )
    topic_id: str | None = None
    question_count: int | None = Field(default=None, ge=1, le=50)


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
    "teaching",
    "community",
    "general_learning",
]

OnboardingState = Literal[
    "not_started",
    "purpose_set",
    "details_set",
    "content_ready",
    "completed",
]

SkillLevel = Literal["beginner", "intermediate", "advanced"]

LlmProvider = Literal["gemini", "openai", "anthropic"]


class LearningProfileResponse(CamelModel):
    id: str
    user_id: str
    onboarding_state: OnboardingState = "not_started"
    purpose: LearningPurpose | None = None
    subjects: list[str] | None = None
    goals_text: str | None = None
    exam_name: str | None = None
    exam_date: date | None = None
    skill_name: str | None = None
    current_level: SkillLevel | None = None
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


class ExamDetailsRequest(CamelModel):
    exam_name: str = Field(min_length=1, max_length=200)
    exam_date: date | None = None
    subjects: list[str] = Field(default_factory=list)
    goals: str | None = None


class SkillDetailsRequest(CamelModel):
    skill_name: str = Field(min_length=1, max_length=200)
    current_level: SkillLevel | None = None
    subjects: list[str] = Field(default_factory=list)
    goals: str | None = None


class OnboardingStatusResponse(CamelModel):
    state: OnboardingState
    progress: dict[str, bool] = Field(default_factory=dict)
    estimated_seconds_remaining: int | None = None
    first_preparation: dict[str, str] | None = None


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

#: Phases a quiz session passes through while it is `GENERATING`. Typed rather than a
#: bare string so the client gets a closed set to switch on: a wait screen that has to
#: guess at stage names cannot report an unknown one honestly. Kept in step with
#: `quiz_engine.GenerationStage`, which owns the order and is asserted against this.
GenerationStage = Literal[
    "PREPARING",
    "REUSING_BANK",
    "WRITING_QUESTIONS",
    "CHECKING_QUESTIONS",
    "READY",
]

PrepMaterialCategory = Literal[
    "TEXTBOOK",
    "NOTES",
    # Scope, weighting, and learning outcomes. Offered by the create wizard as its
    # own category, and materially different from a textbook: a syllabus tells
    # topic extraction what is examinable, not what the answer is.
    "SYLLABUS",
    "PAST_QUESTION",
    "LINK",
    "SLIDE",
    "OTHER",
]

# The three bands of the mastery ladder. Defined here rather than further down
# because topic responses carry a band.
MasteryBand = Literal["focus", "review", "strong"]


class PrepTopicResponse(CamelModel):
    id: str
    prep_id: str
    title: str
    description: str | None = None
    # A grouping heading. `None` for topics extracted before categories existed;
    # the client groups those under no heading rather than inventing one.
    category: str | None = None
    estimated_minutes: int | None = None
    order_index: int
    mastery_score: float | None = None
    # Mastery this topic is aiming for. `None` means "use the preparation's
    # target", which is itself allowed to be `None`.
    target_mastery: float | None = None
    # Derived from `masteryScore` by the shared ladder, so a surface never has to
    # re-implement the 70/80 boundaries and risk disagreeing with the dashboard.
    band: MasteryBand
    status: str
    created_at: datetime


class PrepTopicDetail(PrepTopicResponse):
    """A topic plus its question counts, for the workspace topic list.

    The counts are a separate shape from `PrepTopicResponse` because they are an
    aggregate over other tables rather than columns on the topic, and the write
    paths (`PATCH`, extraction) have no business computing them.
    """

    # Banked questions attributed to this topic.
    question_count: int
    # Of those, how many the learner has answered at least once. Distinct by
    # question, so re-meeting a question in a later session does not inflate it.
    answered_question_count: int


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
    updated_at: datetime


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
    # The workspace labels materials by when they last changed ("Updated
    # yesterday"), which `createdAt` alone cannot express after a relabel.
    updated_at: datetime


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
    target_mastery: float | None = Field(default=None, ge=0, le=100)
    category: str | None = Field(default=None, max_length=120)
    status: str | None = None


PrepareDashboardSection = Literal[
    "summary",
    "preparations",
    "focusTopics",
    "recentSessions",
    "milestones",
]


class PrepareDashboardMeta(CamelModel):
    generated_at: datetime
    degraded_sections: list[PrepareDashboardSection]


# Why a topic is being recommended. A code rather than only prose, so the client
# can choose its own wording and so the reason is assertable in a test.
FocusReason = Literal[
    "NO_TOPICS",
    "NEVER_PRACTISED",
    "LOWEST_MASTERY",
    "MAINTENANCE",
]


class PrepFocusRecommendation(CamelModel):
    """What to practise next in one preparation, and why.

    Only the server knows which topic is weakest and what evidence supports that,
    so this is computed rather than left to the client to guess from a bounded
    focus-topic list that may not even include this preparation.

    `reason` is a short plain-language sentence for surfaces that want one;
    `reasonCode` is the machine-readable form. A client is free to render its own
    copy from the code and ignore the sentence.
    """

    topic_id: str | None = None
    topic_title: str | None = None
    # `None` when there are no topics yet.
    mastery_percent: float | None = None
    band: MasteryBand | None = None
    reason_code: FocusReason
    reason: str
    # The mode a launcher should start, chosen to match the reason.
    recommended_mode: str
    recommended_question_count: int
    # Derived from the topic's own estimate, so it is the learner's plan rather
    # than a made-up duration.
    estimated_minutes: int


class PrepProgressSummary(CamelModel):
    """Derived progress for one preparation.

    Every field comes from `prep_readiness`, the same helper the dashboard and the
    Learn surface use, so a workspace header cannot disagree with the card that
    linked to it.
    """

    progress_percent: float
    average_mastery_percent: float | None
    target_readiness: int | None
    topics_total: int
    topics_strong: int
    topics_review: int
    topics_focus: int
    topics_assessed: int
    questions_answered: int
    accuracy_percent: float | None
    quizzes_taken: int
    practice_minutes: int
    # Consecutive days of completed practice *in this preparation*. Distinct from
    # the dashboard's account-wide streak, and `None` when never practised.
    practice_streak: int | None
    practice_ready: bool


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
    practice_minutes: int
    # The learner's stated goal, or None when they have not set one.
    target_readiness: int | None = None
    # False until topics exist; practice cannot start before then.
    practice_ready: bool
    # What to do next in this preparation. `None` only if the recommendation
    # could not be computed; a preparation with no topics still gets one, telling
    # the learner to extract topics.
    next_action: PrepFocusRecommendation | None = None


class PrepareFocusTopic(CamelModel):
    """A topic worth practising next, weakest first."""

    id: str
    preparation_id: str
    preparation_subject: str
    title: str
    # Grouping heading, `None` for topics extracted before categories existed.
    category: str | None = None
    mastery_percent: float
    target_mastery: float | None = None
    band: MasteryBand
    order_index: int
    # Banked questions attributed to this topic, and how many of them the learner
    # has answered. Both zero for a topic nothing has been generated for yet.
    question_count: int = 0
    answered_question_count: int = 0


class PrepareMilestone(CamelModel):
    """A dated commitment across the learner's active preparations.

    The same derivation as a single preparation's timeline — study-plan items plus
    the target date — flattened across preparations so the dashboard rail does not
    need one request per preparation.
    """

    id: str
    preparation_id: str
    preparation_subject: str
    kind: Literal["STUDY", "EXAM"]
    title: str
    detail: str | None = None
    scheduled_for: datetime
    status: str
    estimated_minutes: int | None = None
    prep_topic_id: str | None = None


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
    # Upcoming and just-past commitments across active preparations, nearest
    # first. Empty when no preparation has a study plan.
    milestones: list[PrepareMilestone]


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
    # Where readiness needed to be on this day to reach the target by the exam.
    # Captured on the day, so raising a target later does not redraw the past.
    # `None` when the preparation had no stated target.
    target_percent: float | None = None
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
    # The preparation's target as it stands now, for the chart's axis and legend.
    # Per-point targets are in `points`, because those are historical.
    target_readiness: int | None = None
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


# ---------------------------------------------------------------------------
# Commercial: the Plus trial
# ---------------------------------------------------------------------------


class TrialShowcaseSuggestion(CamelModel):
    """A Plus capability worth trying, chosen from what this learner actually has."""

    capability_id: str
    title: str
    description: str
    action_url: str
    #: Why this learner specifically. Shown, so a suggestion is not a generic ad.
    reason: str


class TrialStatusResponse(CamelModel):
    """Where the learner stands with the 7-day Plus trial.

    Typed because it was not, and the cost was concrete: the route returned bare
    dicts whose keys differed between branches, so nothing was generated into the
    client's types and the commercial dialog was built against a hand-written
    fixture (`COMMERCIAL_TRIAL_DEMO`) instead. That fixture then announced "Day 3 of
    your Plus trial" to Free learners who had never trialled at all.

    `trialAvailable` is present on every branch and derived from the same rules
    `start_trial` enforces, so the offer shown matches what pressing it would do.
    """

    is_active: bool
    expired: bool = False
    #: Whether starting a trial now would succeed.
    trial_available: bool
    #: 1-7 while a trial runs, 0 otherwise. Never invented for a learner not on one.
    day_number: int = 0
    days_remaining: int = 0
    total_days: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    #: Set only while a cooldown is in force; `None` once eligible again.
    next_trial_available_at: datetime | None = None
    #: Populated only during an active trial — there is nothing to showcase
    #: otherwise, and an empty list says so rather than filling it with examples.
    showcase_suggestions: list[TrialShowcaseSuggestion] = Field(default_factory=list)


class TrialSummaryResponse(CamelModel):
    """What a finished trial delivered. Available only after it ends."""

    trial_days: int
    plus_features_used: list[str]
    learning_outcomes: list[str]
    what_you_would_lose: list[str]
    upgrade_url: str


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
    target_readiness: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Readiness the learner is aiming for by the target date. Optional: "
            "without it, surfaces show readiness with no target line."
        ),
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
    target_readiness: int | None = Field(default=None, ge=0, le=100)


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
    target_readiness: int | None = None
    created_at: datetime
    updated_at: datetime


class PrepDetailResponse(PrepSummaryResponse):
    """One preparation, with everything its workspace header displays.

    The summary shape carries no progress at all, which left the workspace with
    nowhere to read readiness, accuracy or practice time from: those aggregates
    existed only inside the dashboard read model, which is capped and scoped to
    the active set. Rather than making the client assemble a header from a
    dashboard it may not appear in, the detail read returns them directly.

    Progress is a nested object rather than flattened onto this model so that
    "what the learner set up" and "what the learner has achieved" stay visibly
    separate — the first is written by the client, the second never is.
    """

    # `None` once the target date has passed.
    days_until_exam: int | None = None
    progress: PrepProgressSummary
    focus: PrepFocusRecommendation | None = None


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
    # The topic's title, so a runner can label a question without holding the
    # whole topic list and joining on it. `None` for unattributed questions.
    prep_topic_title: str | None = None
    # Safe to show before answering: metadata about the question, not about its
    # answer. `None` for questions banked before difficulty was recorded.
    difficulty: str | None = None
    # Provenance, on the same side of the disclosure boundary as difficulty: that a
    # question came from a 2025 paper says nothing about which option is right.
    # Server-set, so it can be trusted.
    source: str | None = None
    source_year: int | None = None
    # Hints the learner has taken on this question in this session, so a resumed
    # session does not offer a fresh hint they have effectively already had.
    hints_used: int = 0
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
    #: Which phase generation reached, while `status` is `GENERATING`.
    #:
    #: `POST .../quizzes` returns as soon as the session exists and generation
    #: continues in the background, so the client polls this endpoint to follow it.
    #: Decision H set the trigger for that at p95 start latency above 10s; migration
    #: `018` made the figure readable and the first reading was a **p50 of 16.3s**.
    #:
    #: Phase 4e deliberately shipped no staged progress bar until this existed,
    #: because a bar driven by a timer describes state the browser has no access to —
    #: it would read "Writing questions" for a request that had already failed
    #: selecting them. Every value here has a server-side write behind it.
    generation_stage: GenerationStage | None = None
    #: 0.0-1.0, derived from `generationStage` so the two cannot disagree. `None`
    #: when no stage is known, which includes every session created before the
    #: column existed — reporting 0 for those would claim they had not started.
    generation_progress: float | None = None
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
    # Resolved server-side, so the bank tab can group and label by topic without
    # fetching the topic list and joining it row by row.
    prep_topic_title: str | None = None
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


class QuizHintResponse(CamelModel):
    """A hint the learner asked for.

    Deliberately weaker than `explanation`. `nudge` points at the concept or method;
    `eliminatedOption` removes one wrong multiple-choice option and is `null` when
    eliminating would leave no real choice.

    `hintAvailable` is `false` when there is genuinely nothing to offer — better to
    say so than to return a hint-shaped object containing no hint.

    Taking a hint is recorded but is **not** a penalty. It marks the question as
    sitting at the edge of what the learner can currently do.
    """

    question_id: str
    level: int
    nudge: str | None = None
    eliminated_option: str | None = None
    hint_count: int
    hint_available: bool


class AnswerResultResponse(CamelModel):
    """Result for the one question just submitted, including its key.

    This is the only place an answer key is disclosed before the session
    completes, and only for a question the learner has now answered.
    """

    question_id: str
    # Null under examination conditions (`PAST_PAPER_SIM`), where no feedback is
    # given until the session completes. In every other mode these are populated:
    # answering is what earns the answer.
    is_correct: bool | None = None
    correct_answer: str | None = None
    explanation: str | None = None
    # True when this question had already been answered and the stored result
    # was replayed. Resubmitting never re-scores, so a retry is safe and the
    # key returned here cannot be used to raise the score.
    already_answered: bool = False
    # True when the answer was recorded but feedback is being withheld until the end
    # of the session. The client should confirm the answer was received rather than
    # rendering an empty result.
    feedback_deferred: bool = False


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
