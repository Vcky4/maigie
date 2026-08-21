"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# Imported rather than defined here: the knowledge domain needs the same base, and a second copy is
# how the two would drift. See `src/shared/schemas.py` for the two failure modes it prevents.
from src.shared.schemas import CamelModel, PaginatedResponse

# `PaginatedResponse` moved to `src/shared/schemas.py` when the knowledge domain needed it too. Still
# re-exported here so the many `models.PaginatedResponse[...]` references keep working.
__all__ = ["CamelModel", "PaginatedResponse"]


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


class NoteMergeRequest(CamelModel):
    """Combine several of the learner's notes into one.

    The ids are chosen by the learner rather than derived from a rule. There is no provenance on `Note`, so
    the server cannot tell a note written from a voice session apart from one the learner typed, and a
    "merge the short session notes on this topic" feature would have swept up the wrong ones. The person who
    knows which notes belong together is the one who wrote them.

    Bounded on both sides: fewer than two is nothing to combine, and more than ten will not fit in one reply
    — the service says so rather than truncating, and batches are easier to check than one huge rewrite.
    """

    noteIds: list[str] = Field(..., min_length=2, max_length=10)


class NoteTagCountResponse(CamelModel):
    """One tag the learner has used, and how many of their notes carry it.

    The whole catalogue, not the current page. The notes page derived its filter chips from the
    twenty or hundred notes it happened to have loaded, which is truthful about those notes and
    wrong about the library: a tag on note 130 had no chip, and every count was a page count
    wearing a library label.
    """

    tag: str
    count: int


class NoteCaptureDay(CamelModel):
    """Notes created on one day of the learner's week."""

    date: date
    count: int


class NoteSummaryResponse(CamelModel):
    """Library-wide figures for the notes page's tiles and its capture trend.

    Added when search and tag filtering moved server-side. The page counted these from the notes it
    had fetched, which was tolerable while it fetched a hundred unfiltered ones and labelled them
    "Saved to your library" — and would have become plainly wrong the moment the fetch became a
    filtered page, since the tiles would then change as the learner typed.

    Scoped like the default note list: personal notes, not archived, so a tile and the list it sits
    above are counting the same thing.
    """

    total: int
    tagged: int
    linked_to_course: int
    with_attachments: int
    # Oldest first, one entry per day including days with nothing, so a client can render a bar
    # chart without inventing the gaps.
    captured_last_week: list[NoteCaptureDay]


class NoteVersionResponse(CamelModel):
    """A snapshot of a note taken before its content was replaced.

    ``content`` is the note *as it was*, not a diff. Callers render or restore it; the server does
    not attempt to summarise what changed, because a summary of a change to prose is a second thing
    to get wrong.
    """

    id: str
    note_id: str
    title: str
    content: str | None = None
    created_at: datetime


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
    """A stored document.

    Every field below is `NOT NULL` on `GeneratedDocument`, so every field below is required. The
    model previously defaulted eight of them to `None`, which published a weaker contract than the
    table and made each one something the client had to defend against for no reason. Two —
    ``size`` and ``contentType`` — were absent entirely although the worker already serialized
    them, so a file size the database held could not be read back.
    """

    id: str
    user_id: str
    title: str
    format: str
    # Nullable, and the one field here that genuinely is. Documents written before migration 034
    # have no recorded type: the value was sent on every request and dropped, so there is nothing to
    # backfill from except the filename, which is the guessing this column exists to stop.
    doc_type: str | None
    style: str
    filename: str
    file_url: str
    preview_url: str
    size: int
    content_type: str
    # Present on every document, private ones included. ``isPublic`` decides whether it resolves.
    share_id: str
    is_public: bool
    created_at: datetime


# `DocumentListResponse` is gone. It was the third pagination envelope in the codebase — hand-rolled
# `pageSize`, no `pages`, a plain `BaseModel` — for a list that is paginated exactly like notes and
# saved resources. `PaginatedResponse[DocumentResponse]` replaces it.


class DocumentFormatCountResponse(CamelModel):
    """One output format the learner has produced, and how many documents use it."""

    format: str
    count: int


class DocumentSummaryResponse(CamelModel):
    """Library-wide figures for the document page's tiles.

    Every one of these was previously counted in the browser from whichever page had been
    fetched, and then labelled as a library figure — "In your library", "Shared publicly". With
    one page and no pager that was merely optimistic; with a pager it is simply wrong. They are
    counted here instead, in one round trip.

    ``month_start`` is published rather than assumed: "this month" is a claim about the learner's
    wall clock, and the zone is only known when they have told us. Returning the instant the count
    was measured from lets the page say *since when* instead of asserting a calendar month it
    cannot prove.
    """

    total: int
    published: int
    created_this_month: int
    month_start: datetime
    formats: list[DocumentFormatCountResponse]


class DocumentJobQueuedResponse(CamelModel):
    """The reply to queueing a generation job."""

    task_id: str
    status: Literal["queued"]


class DocumentJobStatusResponse(CamelModel):
    """The state of a queued generation job, and its document once there is one.

    ``result`` is a `DocumentResponse`, which is the point of this model existing. Both job routes
    used to return bare dicts, so neither reached the generated client types, and the worker's
    snake_case Celery payload went to the browser untranslated — where the web client kept a
    hand-written second document type and a mapper to convert it. One response model deletes both.
    """

    task_id: str
    status: Literal["queued", "running", "success", "failed"]
    result: DocumentResponse | None = None
    error: str | None = None


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
    # The three review aids, each restored with migration 026 because each backed a piece of the
    # review page that was deleted for having no column: a hint revealed on demand, an explanation
    # shown with the answer, and a mnemonic beside it. Null means the card has none, and the reader
    # omits the control rather than offering a blank one.
    hint: str | None = None
    explanation: str | None = None
    memoryHook: str | None = None


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
    # The three review aids, each restored with migration 026 because each backed a piece of the
    # review page that was deleted for having no column: a hint revealed on demand, an explanation
    # shown with the answer, and a mnemonic beside it. Null means the card has none, and the reader
    # omits the control rather than offering a blank one.
    hint: str | None = None
    explanation: str | None = None
    memory_hook: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def interval_previews(self) -> dict[str, int]:
        """What each of the four review buttons would do to this card, in days.

        A computed field rather than a column, because there is nothing to store: SM-2 is a
        deterministic function of `intervalDays`, `repetitionCount` and `easeFactor`, which this
        response already carries. The review page showed these under each button and they were deleted
        as "unbacked by any persisted field" — the wrong conclusion, since the arithmetic needed no
        field at all.

        Derived here rather than in the client for one reason: a preview computed by a second
        implementation is a second scheduler, and the moment the two disagree the page promises an
        interval the card will not get. `flashcard_service.project_review` is the only place the rule
        lives, and both the preview and the grade that follows it go through it.
        """
        from .services.flashcard_service import review_interval_previews

        return review_interval_previews(
            interval_days=self.interval_days,
            repetition_count=self.repetition_count,
            ease_factor=self.ease_factor,
            lapse_count=self.lapse_count,
        )


class FlashcardUpdate(BaseModel):
    """Partial update of a card. Omitted fields are left alone.

    ``deckId`` is how a card moves between decks, and an explicit ``null`` is a
    meaningful value for it — detaching the card from every deck. Routes therefore
    dump these with ``exclude_unset=True``, which is what keeps "leave the deck
    alone" distinguishable from "remove it from its deck"; a plain ``None`` default
    read without that flag would collapse the two.
    """

    front: str | None = Field(default=None, min_length=1)
    back: str | None = Field(default=None, min_length=1)
    deckId: str | None = None
    # Editable, and an explicit null clears one — same `exclude_unset` contract as `deckId`, so
    # "leave the hint alone" stays distinguishable from "remove the hint".
    hint: str | None = None
    explanation: str | None = None
    memoryHook: str | None = None


class FlashcardReviewRequest(CamelModel):
    quality: int = Field(ge=0, le=5)


class FlashcardStats(CamelModel):
    """Flashcard counts for the whole library, or for one deck.

    Previously this model declared four fields while the service returned eight, so
    the streak and weekly counts it computed were dropped on the way out and no
    client could ever see them.
    """

    total: int
    due_today: int
    # Mastered, learning and new partition the library exactly.
    mastered_count: int
    learning_count: int
    new_count: int
    average_ease_factor: float
    # Mean of each reviewed card's most recent grade, as a percentage of the 0-5
    # scale. Null when nothing has been reviewed: a library with no reviews has no
    # recall, and 0% would report the learner as failing everything.
    recall_percent: int | None = None
    reviewed_card_count: int
    # Counted from the review log, so they are zero until reviews accumulate after
    # migration 020 rather than being back-filled from invented history.
    reviewed_total: int
    reviewed_this_week: int
    active_days_this_week: list[str]
    current_streak: int


class DeckCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    # The learner's own grouping label, and the colour they picked. Both optional,
    # because a deck without them is complete; the client derives a colour when the
    # learner expressed no preference.
    subject: str | None = None
    accent: Literal["violet", "orange", "blue", "green"] | None = None
    dailyGoal: int | None = Field(default=None, ge=1, le=200)
    courseId: str | None = None
    topicId: str | None = None
    prepId: str | None = None


class DeckUpdate(BaseModel):
    """Partial update of a deck. Read with ``exclude_unset=True``, so an explicit
    ``null`` clears a field and an omitted key leaves it untouched."""

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    subject: str | None = None
    accent: Literal["violet", "orange", "blue", "green"] | None = None
    dailyGoal: int | None = Field(default=None, ge=1, le=200)


class DeckResponse(CamelModel):
    id: str
    user_id: str
    title: str
    description: str | None = None
    subject: str | None = None
    accent: str | None = None
    daily_goal: int | None = None
    course_id: str | None = None
    topic_id: str | None = None
    prep_id: str | None = None
    # What the server created this deck for, or null when the learner made it by hand.
    # Deliberately absent from `DeckCreate`: origin is provenance the server assigns,
    # and letting a client claim one would let it take another deck's origin slot.
    #
    # Doubles as the "created automatically" signal the clients label decks with, which
    # is why there is no separate `isAuto` field.
    origin_type: str | None = None
    origin_id: str | None = None
    # Aggregated in a single grouped query by the deck listing.
    card_count: int
    due_count: int
    mastered_count: int
    # Cards in this deck that have been reviewed at least once. Present because it is
    # the denominator of `recallPercent`, which is otherwise uninterpretable.
    reviewed_count: int
    # Null for a deck with no reviewed cards; see `FlashcardStats.recallPercent`.
    recall_percent: int | None = None
    mastery_percent: int
    # Null for a deck that has never been reviewed, and for an empty deck there is
    # nothing scheduled either.
    last_reviewed_at: datetime | None = None
    next_review_at: datetime | None = None
    # Derived server-side so every surface agrees on what makes a deck "strong":
    # `due` when anything is due now, `strong` at or above the mastery threshold,
    # otherwise `learning`. An empty deck is `learning` — it is not strong, and it
    # has nothing due.
    status: Literal["due", "learning", "strong"]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Flashcards dashboard (composed read for the flashcards page)
# ---------------------------------------------------------------------------


FlashcardsDashboardSection = Literal[
    "review", "stats", "decks", "forecast", "activity", "deckMastery", "insight"
]


class FlashcardsTimezoneMeta(CamelModel):
    """Which zone the day-based figures were computed in, and whether it is known.

    Streaks, "this week" and the forecast are claims about the learner's calendar.
    When the zone was never captured these fall back to UTC, and the client is told
    so rather than being left to assume the boundaries are local.
    """

    name: str
    is_known: bool


class FlashcardsDashboardMeta(CamelModel):
    generated_at: datetime
    degraded_sections: list[FlashcardsDashboardSection]
    timezone: FlashcardsTimezoneMeta
    # True once the review log holds at least one row for this learner. Until then,
    # streak, weekly counts and activity are legitimately empty rather than zero
    # because nothing happened, and the client can say which.
    has_review_history: bool


class FlashcardReviewSummary(CamelModel):
    due_today: int
    overdue: int
    # Thirty seconds per due card, the same constant the Learn dashboard uses, so the
    # two surfaces cannot quote different estimates for the same queue.
    estimated_minutes: int
    retention_percent: int | None = None
    review_streak: int
    reviewed_this_week: int


class FlashcardLibraryStats(CamelModel):
    total_cards: int
    mastered_cards: int
    learning_cards: int
    new_cards: int
    average_ease: float
    mastered_percent: int | None = None
    # Cards in no deck, and the field that makes this payload self-consistent.
    #
    # Every figure above is scoped to the learner and therefore counts unfiled cards,
    # while `decks` is a LEFT JOIN from `FlashcardDeck` and structurally cannot. Before
    # this field the difference was unexplainable from the response: the page showed
    # cards due with no deck holding them and nothing to say why. Generation now files
    # cards by origin, so this should trend to zero, but it cannot be assumed zero —
    # deleting a deck deliberately detaches its cards into exactly this state.
    unfiled_cards: int = 0


class FlashcardForecastDay(CamelModel):
    date: date
    # Short weekday name in the learner's zone, so the client does not have to
    # recompute a label that depends on the same zone the counts were bucketed in.
    weekday: str
    is_today: bool
    due: int
    new_cards: int


class FlashcardActivityEntry(CamelModel):
    """Something the learner did to one deck on one local day.

    Three kinds, because the feed shows three kinds of progress and a feed that only
    ever reported reviews would go silent for a learner who spent the week writing
    cards:

    - ``reviewed`` — cards graded. ``recallPercent`` and ``lapseCount`` apply.
    - ``graduated`` — cards that crossed into maturity, detected as a transition
      between consecutive reviews rather than read from a stored flag.
    - ``created`` — cards added, from ``Flashcard.createdAt``.

    Derived at read time, not persisted. A "session" has no start or end the server
    observed, so inventing a session entity would claim more than is known; grouping
    events by deck and calendar day claims exactly what the rows say. ``kind`` is
    carried so the client can label an entry without inferring it from which fields
    happen to be null.
    """

    id: str
    kind: Literal["reviewed", "graduated", "created"]
    deck_id: str | None = None
    deck_title: str | None = None
    occurred_at: datetime
    card_count: int
    # Only meaningful for `reviewed`. Null elsewhere, because a day spent writing
    # cards has no recall figure rather than a recall figure of zero.
    recall_percent: int | None = None
    lapse_count: int = 0


class DeckMasterySummary(CamelModel):
    deck_id: str
    title: str
    subject: str | None = None
    mastery_percent: int
    # Change in mastery over the trailing window, in percentage points. Null when the
    # review log has nothing before the window opened, which is the honest answer for
    # a deck whose earlier state was never recorded.
    change_percent: int | None = None


class FlashcardInsight(CamelModel):
    """A statement about this learner's own reviews, chosen by a fixed ladder.

    ``kind`` is present so the client can style or suppress a category without
    parsing the prose, and so it is obvious in a response which rule fired. Every
    variant is computed from persisted rows; none is generated text.
    """

    kind: Literal[
        "best_time_of_day",
        "overdue_backlog",
        "due_now",
        "lapsing_cards",
        "library_summary",
        "empty_library",
    ]
    title: str
    body: str
    action_label: str


class FlashcardsDashboardResponse(CamelModel):
    meta: FlashcardsDashboardMeta
    review: FlashcardReviewSummary
    stats: FlashcardLibraryStats
    decks: list[DeckResponse]
    forecast: list[FlashcardForecastDay]
    activity: list[FlashcardActivityEntry]
    deck_mastery: list[DeckMasterySummary]
    insight: FlashcardInsight


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


#: What a plan item's status may be set to.
StudyPlanItemStatus = Literal["PENDING", "COMPLETED", "SKIPPED"]

#: What a learner may set a plan's status to. `SUPERSEDED` is absent on purpose — it is
#: written by regeneration, never chosen.
SettableStudyPlanStatus = Literal["ACTIVE", "PAUSED", "COMPLETED"]


#: An ISO weekday: 1 = Monday ... 7 = Sunday. Bounded in the type so an out-of-range day
#: is a `400` naming the field, rather than a value the scheduler quietly never matches.
IsoWeekday = Annotated[int, Field(ge=1, le=7)]


class StudyPlanCreate(BaseModel):
    title: str = Field(min_length=1)
    goalDescription: str | None = None
    deadline: str
    prepId: str | None = None
    # Minutes a week the learner intends to study, from the create wizard's pace and
    # session-length choices. Optional, because it is an intention: with none set, a
    # surface reports minutes planned without a target instead of inventing one.
    weeklyGoalMinutes: int | None = Field(default=None, ge=15, le=10_080)
    # --- The rhythm, from steps 1 and 2 of the wizard ---
    #
    # Sent as well as `weeklyGoalMinutes`, not instead of it: when both of these are
    # present the server derives the weekly goal from them, so the "35 min - 5x week" line
    # and the weekly total cannot disagree. Kept separate because their product cannot be
    # factorised back.
    sessionsPerWeek: int | None = Field(default=None, ge=1, le=7)
    sessionMinutes: int | None = Field(default=None, ge=5, le=600)
    # ISO weekday numbers the learner is available: 1 = Monday ... 7 = Sunday.
    #
    # `min_length=1`, so omitting the field means "did not say" and the schedule uses every
    # day, while sending `[]` is refused. An empty list would mean no day is acceptable,
    # which is not a plan that can be built, and storing it would leave the scheduler to
    # silently ignore it — the discard this whole change is closing.
    preferredDays: list[IsoWeekday] | None = Field(default=None, min_length=1, max_length=7)
    # The path shape chosen in step 1. Validated against the catalogue because the value
    # is not merely recorded: its phases are given to the generator as the structure to
    # follow, so an id with no catalogue entry would silently produce an ungrouped plan
    # under a heading promising the opposite.
    shape: str | None = None
    # Courses the learner chose to link in step 3 of the wizard. Ids they do not own are
    # rejected rather than silently dropped, so a selection that cannot be honoured is
    # reported instead of disappearing.
    courseIds: list[str] = Field(default_factory=list, max_length=50)
    # The wizard's two toggles. Both drive real behaviour: completing an item generates
    # review cards into a deck owned by the plan, and a daily sweep sends a check-in
    # notification seven days after the last one.
    generateReviewCards: bool = False
    weeklyCheckIn: bool = False


class StudyPlanUpdate(BaseModel):
    """Partial update of a plan. Read with ``exclude_unset=True``.

    Changing `deadline` redistributes pending items, because leaving them put would
    produce a schedule with work sitting past the plan's own deadline. Changing
    `sessionMinutes` or `preferredDays` redistributes for the same reason: both decide
    which day an item lands on, so a plan that kept its old dates would contradict the
    rhythm it now says it has.

    `shape` is absent deliberately. It is the structure the plan's phases were generated
    against, so changing it later would leave a plan labelled with one shape and grouped
    by another's phases. Re-shaping means regenerating.
    """

    title: str | None = Field(default=None, min_length=1)
    goalDescription: str | None = None
    deadline: datetime | None = None
    status: SettableStudyPlanStatus | None = None
    weeklyGoalMinutes: int | None = Field(default=None, ge=15, le=10_080)
    sessionsPerWeek: int | None = Field(default=None, ge=1, le=7)
    sessionMinutes: int | None = Field(default=None, ge=5, le=600)
    preferredDays: list[IsoWeekday] | None = Field(default=None, min_length=1, max_length=7)
    generateReviewCards: bool | None = None
    weeklyCheckIn: bool | None = None


class StudyPlanCourseLinkRequest(BaseModel):
    courseIds: list[str] = Field(min_length=1, max_length=50)


class StudyPlanItemCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    scheduledDate: datetime
    estimatedMinutes: int = Field(default=30, ge=5, le=600)
    itemType: str = "STUDY"
    phase: str | None = None


class StudyPlanItemUpdate(BaseModel):
    """Partial update of an item. Read with ``exclude_unset=True``."""

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    scheduledDate: datetime | None = None
    estimatedMinutes: int | None = Field(default=None, ge=5, le=600)
    phase: str | None = None
    status: StudyPlanItemStatus | None = None


class StudyPlanItemResponse(CamelModel):
    """Mirrors the persisted item; only genuinely nullable columns are optional."""

    id: str
    plan_id: str
    title: str
    description: str | None = None
    scheduled_date: datetime
    estimated_minutes: int
    item_type: str
    # The grouping label this item belongs to. Null for items generated before phases
    # existed, and for plans the generator did not group — such a plan is one flat list,
    # which is what it is.
    phase: str | None = None
    topic_id: str | None = None
    prep_topic_id: str | None = None
    status: str
    completed_at: datetime | None = None


class StudyPlanCourseLink(CamelModel):
    """A course linked to a plan.

    The title is read from `Course` on every request rather than copied onto the link
    row, so a renamed course reads correctly here instead of showing the name it had when
    it was linked.
    """

    course_id: str
    title: str
    difficulty: str | None = None
    linked_at: datetime


class StudyPlanMaterialResponse(CamelModel):
    """A reference file attached to a plan."""

    id: str
    plan_id: str
    filename: str
    url: str
    file_type: str | None = None
    size: int | None = None
    created_at: datetime


class StudyPlanPhaseSummary(CamelModel):
    """One phase of a plan, with everything about it that its items imply.

    Nothing here is stored. A phase is a nullable label on `StudyPlanItem`, and its
    ordinal, span and progress are all derived from the items carrying that label — which
    is the argument for the label over a `StudyPlanPhase` table: a table would hold a name
    and a foreign key and compute the rest from these same rows, while adding a second
    thing that can disagree with them about order.
    """

    label: str
    #: Position in the plan, 1-based, ordered by when the phase's work starts.
    number: int
    #: The first and last scheduled dates of the phase's items. A week range is presentation
    #: — "Week 3-5" depends on where the reader thinks week one began — so the dates are
    #: returned and the client words them.
    start: datetime
    end: datetime
    total_items: int
    completed_items: int


class StudyPlanNextItem(CamelModel):
    """The earliest still-pending item of a plan: what "Up next" means.

    Absent when nothing is pending, rather than a blank line, because "finished" and
    "everything was skipped" both want saying and neither is "up next: nothing".
    """

    id: str
    title: str
    scheduled_date: datetime
    item_type: str
    estimated_minutes: int
    phase: str | None = None


class StudyPlanSummaryResponse(CamelModel):
    """A plan without its items, for list views.

    The items are deliberately absent. A plan card shows counts, which are stored on the
    plan, so embedding every item of every plan made an "all plans" page pay for
    hundreds of rows it never rendered. `GET /study-plans/{id}` returns them.
    """

    id: str
    user_id: str
    title: str
    goal_description: str | None = None
    deadline: datetime
    prep_id: str | None = None
    status: str
    # `ADAPTIVE` or `EVEN` — which scheduler produced this plan. Exposed because
    # adaptive scheduling is a paid claim, and a client that cannot read this cannot
    # tell the learner which one they got. Null for plans predating the column.
    strategy: str | None = None
    weekly_goal_minutes: int | None = None
    # The rhythm the learner chose, all null for plans created before it was stored. On the
    # summary rather than only the detail response because the plan card prints the session
    # design, and a card that had to fetch the plan to render it would defeat the point of
    # a summary.
    sessions_per_week: int | None = None
    session_minutes: int | None = None
    preferred_days: list[int] | None = None
    # The path shape this plan's phases were generated against, e.g. `skill-mastery`.
    shape: str | None = None
    skills: list[str] | None = None
    # The wizard's toggles, each backed by behaviour rather than stored intent.
    generate_review_cards: bool
    weekly_check_in: bool
    # The deck this plan generates review cards into. Null until the first card is
    # generated, or if that deck was later deleted.
    review_deck_id: str | None = None
    last_check_in_at: datetime | None = None
    total_items: int
    completed_items: int
    # Derived from the plan's items, aggregated in SQL rather than by loading them — the
    # point of this response is that it does not carry the items, and a card that had to
    # fetch them to print its phase label would undo that.
    #
    # Both are null for a plan with nothing to say: `currentPhase` when the generator did
    # not group the plan, `nextItem` when nothing is pending.
    current_phase: StudyPlanPhaseSummary | None = None
    total_phases: int = 0
    next_item: StudyPlanNextItem | None = None
    created_at: datetime
    updated_at: datetime


class StudyPlanResponse(StudyPlanSummaryResponse):
    """A plan with its items, linked courses and reference files.

    `deadline`, `totalItems`, and `completedItems` are NOT NULL columns, and plan
    queries eagerly load `items`, so these are always serialized.

    `linkedCourses` and `materials` are on the detail response and not the summary, for
    the same reason `items` is: a list card shows neither, and loading them for every
    plan on an all-plans page would be work nothing renders.
    """

    items: list[StudyPlanItemResponse]
    linked_courses: list[StudyPlanCourseLink] = Field(default_factory=list)
    materials: list[StudyPlanMaterialResponse] = Field(default_factory=list)


class PlanShapePhase(CamelModel):
    """One phase of a path shape, as previewed in the wizard before the plan exists.

    `duration` is a human week range for the preview and is not used for scheduling: real
    dates come from the deadline and the learner's available days, so a shape is not in a
    position to promise a timetable.
    """

    id: str
    title: str
    description: str
    duration: str
    outcomes: list[str]


class PlanShapeResponse(CamelModel):
    """A path shape offered by the create wizard.

    Served from the backend rather than held in the client because the generator is given
    these phase titles as the structure to fill. While the catalogue lived only in the web
    bundle, step 4 previewed one roadmap and the plan was built with another.
    """

    id: str
    title: str
    category: str
    description: str
    default_title: str
    default_outcome: str
    phases: list[PlanShapePhase]


class StudyPlanWeekItem(CamelModel):
    """One of the featured plan's items falling in the current week.

    The raw item, not a formatted day card. Whether a day reads as done, today or still to
    come follows from `status` and `scheduledDate`, and the weekday name is a locale
    decision — both belong to whoever is drawing it.
    """

    id: str
    plan_id: str
    title: str
    scheduled_date: datetime
    estimated_minutes: int
    item_type: str
    status: str


class StudyPlansDashboardResponse(CamelModel):
    """Everything the plan library page shows above its grid, in one request.

    Composed for the same reason as the flashcards dashboard: assembled from the endpoints
    that already existed, this page was six requests — the list, the list again filtered to
    active, again to completed, the due-today list, the featured plan, and its metrics — and
    it still could not produce the weekly figure, because `StudyPlanMetrics.completedMinutes`
    is all-time.

    `weeklyMinutes` is **planned** minutes on work completed since the start of the learner's
    week, in their own timezone. Planned, because nothing measures time at a desk; theirs,
    because a week that begins at UTC midnight is the wrong week for most of the world.
    """

    #: Planned minutes on items completed since the learner's week began.
    weekly_minutes: int
    #: Summed target across active plans. Null when no active plan states one, rather than
    #: zero, so a client shows minutes done without a target instead of dividing by nothing.
    weekly_goal_minutes: int | None = None
    #: Pending items due today or earlier, across active plans. Overdue work is work waiting.
    tasks_due: int
    active_count: int
    paused_count: int
    completed_count: int
    #: The active plan with the nearest deadline, or null when there is no active plan.
    featured: StudyPlanSummaryResponse | None = None
    #: The featured plan's own streak: consecutive days it had an item completed, in the
    #: learner's timezone. Not the flashcard review streak, which counts a different activity.
    featured_streak_days: int = 0
    #: The featured plan's items for the current week.
    featured_week: list[StudyPlanWeekItem] = Field(default_factory=list)
    #: False when the learner's timezone was never captured, so the weekly boundaries above
    #: are a UTC assumption rather than their actual week. Surfaced rather than hidden,
    #: because a "this week" figure computed in the wrong week is wrong silently.
    timezone_known: bool = False


class StudyPlanMetricsResponse(CamelModel):
    """Plan-scoped progress figures, derived from the plan's own items.

    `completedMinutes` is **planned** effort on completed work, not measured time — no
    part of this observes how long a learner actually spent, and a client must not label
    it as focus or time-at-desk. Measured session time lives in `/analytics/sessions`.

    There is no retention figure. Retention is flashcard recall, a different domain, and
    a plan with no flashcards has none; the honest options are to omit it or to show
    library recall from `GET /flashcards/stats` under a heading that says so.
    """

    completed_minutes: int
    planned_minutes: int
    practice_completed: int
    skipped_items: int
    # Consecutive days ending today, or yesterday if today is unused. Counts completed
    # plan items — deliberately not the flashcard review streak, which measures
    # something else.
    current_streak_days: int
    active_days: int


class StudyPlanTodayItem(CamelModel):
    """One pending item due today or earlier, with the plan it came from.

    The plan's title travels with the item because a cross-plan list is unreadable
    without it, and fetching each plan separately would be a query per row.
    """

    item: StudyPlanItemResponse
    plan_id: str
    plan_title: str
    plan_deadline: datetime


# ===========================================================================
# Reflections
# ===========================================================================


class ReflectionType(str, Enum):
    """Lowercase, because that is what the API filters on and what clients publish.

    An unconstrained `String` let the Sunday task write `"WEEKLY"` past a service that
    branches on `"weekly"`, so every scheduled reflection took the default period and then
    failed the equality filter on the list endpoint.
    """

    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReflectionDepth(str, Enum):
    STANDARD = "standard"
    DEEP = "deep"


class ReflectionActionKind(str, Enum):
    """What a recommended action does. The client turns this into a URL.

    A closed set rather than a path string: a backend that emits `/prepare/x/practice/weak`
    owns the web client's routing, and the same value would be wrong on any other client.

    `NONE` is legitimate — an action can be advice with nowhere to go, and the card renders
    without a button.
    """

    PREPARATION_PRACTICE = "preparation_practice"
    STUDY_PLAN = "study_plan"
    SCHEDULE = "schedule"
    FLASHCARD_REVIEW = "flashcard_review"
    COURSE = "course"
    NOTE = "note"
    GOAL = "goal"
    NONE = "none"


class ReflectionActionTarget(CamelModel):
    """Where an action points, as data rather than as a path.

    Explicit optional fields rather than a `params: dict`, because an untyped bag is the
    defect `ReflectionMetrics` exists to remove and putting one back on the way out would be
    an odd trade.
    """

    kind: ReflectionActionKind = ReflectionActionKind.NONE
    entity_id: str | None = None
    #: Practice mode, meaningful only for the practice kinds.
    mode: str | None = None


class ReflectionAction(CamelModel):
    """One prescriptive next step.

    The model writes `title`, `detail` and `label`. The service chooses `target` from the
    metrics it already computed — a model free to name an entity would eventually cite one
    the learner does not own, which is an authorization bug dressed as a recommendation.
    """

    id: str
    title: str
    detail: str
    label: str
    target: ReflectionActionTarget = Field(default_factory=ReflectionActionTarget)


class ReflectionMetrics(CamelModel):
    """Everything a reflection measures. Every field is derived from persisted rows.

    Replaces `activitiesLayer` / `progressLayer` / `achievementsLayer`, which were untyped
    dicts filled by a language model that had not been shown the data it was counting.

    **Every field is nullable and `None` means "not measured", never "zero".** A learner who
    reviewed no cards and a learner whose card count was never computed are different
    situations, and a reflection that reports zeros for the second is making a judgement
    about the first. The list fields follow the same rule: `[]` is "measured, nothing
    found", `None` is "not looked at".

    All-null is therefore a valid and honest state, and is what Phase 1 writes until the
    aggregate queries land.
    """

    # --- Activities: what the learner did
    focused_minutes: int | None = None
    active_days: int | None = None
    sessions_completed: int | None = None
    topics_studied: int | None = None
    notes_created: int | None = None
    flashcards_reviewed: int | None = None
    quizzes_completed: int | None = None

    # --- Progress: what changed because of it
    topics_mastered: int | None = None
    new_topics_mastered: list[str] | None = None
    mastery_gained_percent: float | None = None
    recall_percent: float | None = None
    accuracy_percent: float | None = None
    consistency_score: float | None = None
    average_session_minutes: float | None = None
    best_day: str | None = None
    goals_advanced: int | None = None

    # --- Achievements: what was reached
    streak_current: int | None = None
    streak_best: int | None = None
    milestones_reached: list[str] | None = None


# ---------------------------------------------------------------------------
# The narrative — prose, with every number arriving from SQL beside it
# ---------------------------------------------------------------------------
#
# Typed rather than a `dict`, and the reason is on the record: the three untyped JSON columns
# this replaces were filled with snake_case keys inside a camelCase payload, so the one client
# reading them got `undefined` for every field, and four of the keys it wanted were never
# written under any spelling. A `dict` cannot be wrong, which is exactly the problem — nothing
# can disagree with it, so nothing reports when it drifts.
#
# The split inside these models is Decision A and it is the whole point. Every prose field is
# model-authored. Every numeric field is measured and passed *in* to the composer. A model that
# is handed `mastery` and asked for `insight` cannot invent a figure; a model asked for both
# will, and did — that is the defect this programme exists to close.


class ReflectionSignal(CamelModel):
    """One measured figure with a sentence explaining it."""

    id: str
    title: str
    #: Measured. `None` when nothing observed it — never zero as a stand-in.
    value: float | None = None
    unit: str | None = None
    #: Model-authored. What the figure means for this learner.
    description: str | None = None
    #: Model-authored, but constrained to restating figures it was given.
    evidence: str | None = None


class ReflectionSubjectInsight(CamelModel):
    """A subject line on the reflection: its numbers, and a sentence about them."""

    id: str
    title: str
    category: str | None = None
    #: Both measured — `mastery` from the course's topics, `change` by differencing two daily
    #: snapshots. The model receives them and supplies `insight` only.
    mastery: float | None = None
    change: float | None = None
    insight: str | None = None


class ReflectionRhythmDay(CamelModel):
    """One day of the week strip. Entirely measured, from `DailyLearningSnapshot`."""

    day: str
    minutes: float | None = None
    active: bool = False


class ReflectionPattern(CamelModel):
    """Something to keep doing, or to watch. Prose, with the figures it rests on named."""

    title: str
    body: str
    evidence: str | None = None


class ReflectionPatterns(CamelModel):
    """`keep` and `watch`, each optional.

    Optional because a learner with two days of history has no pattern yet, and asserting one
    would be the model filling a slot rather than describing anything.
    """

    keep: ReflectionPattern | None = None
    watch: ReflectionPattern | None = None


class ReflectionNarrative(CamelModel):
    """The written half of a reflection.

    Persisted whole in one JSON column and never queried by field, which is why it is a column
    rather than five tables: nothing filters on `closing`.

    Null on the response when narration failed. That is a real and important state — Phase 1
    established that a reflection whose prose could not be written still has genuine measured
    metrics worth keeping, so the failure must be representable rather than papered over with an
    apology sentence that reads like a reflection.
    """

    opening: list[str] = Field(default_factory=list)
    #: A short label for the period's character, e.g. "consolidation". Model-authored.
    theme: str | None = None
    #: How the period compared with the one before, as words. The *number* behind it is in
    #: `metrics`; this is the reading of it.
    change_label: str | None = None
    signals: list[ReflectionSignal] = Field(default_factory=list)
    subjects: list[ReflectionSubjectInsight] = Field(default_factory=list)
    rhythm: list[ReflectionRhythmDay] = Field(default_factory=list)
    patterns: ReflectionPatterns = Field(default_factory=ReflectionPatterns)
    #: Formatted from measurements, not written. `reflection_metrics.build_highlights` produces
    #: these, because they read as bare facts — "12-day learning streak" — and a model writing
    #: them would be inventing statistics in the place they look most credible.
    highlights: list[str] = Field(default_factory=list)
    closing: str | None = None


class ReflectionResponse(CamelModel):
    id: str
    user_id: str
    type: ReflectionType
    period_start: datetime
    period_end: datetime
    title: str | None = None
    summary: str
    depth: ReflectionDepth
    metrics: ReflectionMetrics = Field(default_factory=ReflectionMetrics)
    #: Null until a narrative has been composed for this row, and null when composing it
    #: failed. Distinct from an empty narrative, which would claim prose was written and say
    #: nothing.
    narrative: ReflectionNarrative | None = None
    recommendations: list[ReflectionAction] = Field(default_factory=list)
    opened_at: datetime | None = None
    created_at: datetime


class ReflectionGenerateRequest(CamelModel):
    type: ReflectionType = Field(description="weekly or monthly")


class ReflectionUpdate(CamelModel):
    """Rename a reflection, or correct its summary. Metrics are not editable by anyone.

    Nothing writes `metrics` through the API: it is measured, so a route that let a client
    set it would reopen the exact hole this stage closes.
    """

    title: str | None = None
    summary: str | None = None


# ---------------------------------------------------------------------------
# Reflection notes — what the learner wrote, not what was generated
# ---------------------------------------------------------------------------


class ReflectionNoteResponse(CamelModel):
    id: str
    user_id: str
    body: str
    prompt_used: str | None = None
    created_at: datetime
    updated_at: datetime


class ReflectionNoteCreate(CamelModel):
    """A note the learner typed, optionally against one of the starter prompts."""

    #: Bounded at both ends. `min_length=1` because an empty note is a mis-click rather than a
    #: thought, and accepting it would put a blank card in the learner's journal; the ceiling is
    #: generous but present, since an unbounded Text field reachable by an authenticated POST is
    #: a storage cost anyone can set.
    body: str = Field(min_length=1, max_length=10_000)
    #: The starter prompt this note answers, when one seeded it.
    prompt_used: str | None = Field(default=None, max_length=500)


class ReflectionNoteUpdate(CamelModel):
    """Edit the text of a note.

    `promptUsed` is deliberately absent. It records what the learner was answering when they
    wrote, which is a fact about that moment; letting a later edit rewrite it would make the
    field useless for the one question it exists to answer.
    """

    body: str = Field(min_length=1, max_length=10_000)


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


#: Rows written before `entityType`/`entityId` existed carry an id under a key each service chose for
#: itself. Mapped here, in one place, so historical entries are routeable too — and so no consumer has
#: to learn six key names, which is the whole point of the pair.
_LEGACY_ACTIVITY_ENTITY_KEYS: list[tuple[str, str]] = [
    ("noteId", "note"),
    ("docId", "document"),
    ("cardId", "flashcard"),
    ("planId", "study_plan"),
    ("prepId", "preparation"),
    ("quizId", "quiz"),
]


class ActivityFeedEntryResponse(CamelModel):
    id: str
    user_id: str
    activity_type: str
    title: str
    description: str | None = None
    context: dict | None = None
    occurred_at: datetime

    #: What this entry is about, and which row — the two fields that make an entry clickable.
    #:
    #: Nullable, and the nullability is a fact rather than a hedge: entries written before
    #: `activity_feed_service.record` required them have no recorded artifact, and for a few of those
    #: the context holds no id at all. A client renders those as text, which is what they are.
    entity_type: str | None = None
    entity_id: str | None = None

    @model_validator(mode="after")
    def _derive_entity(self) -> "ActivityFeedEntryResponse":
        context = self.context or {}
        if not self.entity_type:
            self.entity_type = context.get("entityType")
        if not self.entity_id:
            self.entity_id = context.get("entityId")
        if self.entity_id:
            return self
        for key, entity_type in _LEGACY_ACTIVITY_ENTITY_KEYS:
            value = context.get(key)
            if value:
                self.entity_type = self.entity_type or entity_type
                self.entity_id = value
                break
        return self


#: The activity feed's page. `PaginatedResponse` rather than a fourth hand-written envelope — it already used
#: `items`, so the only change on the wire is that `pages` appears, which no consumer can be broken by.
ActivityFeedResponse = PaginatedResponse[ActivityFeedEntryResponse]


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


# `LearningPathTopicResponse` and `LearningPathResponse` were removed with the route
# that used them, `GET /learning/courses/{id}/path`, which raised `501` unconditionally.
# A response model with no route describes a shape nothing returns, and leaving it in the
# schema advertises an endpoint that does not exist. A path through material is a study
# plan; course structure is `GET /api/v1/knowledge/courses/{id}`.


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
    item_count: int
    entity_types: list[str]


# ===========================================================================
# Collections
# ===========================================================================


class CollectionCreate(CamelModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class CollectionUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class CollectionItemAdd(CamelModel):
    entity_type: Literal["note", "deck", "saved_resource", "document"]
    entity_id: str


class CollectionReorder(CamelModel):
    item_ids: list[str]


class CollectionItemResponse(CamelModel):
    id: str
    entity_type: str
    entity_id: str
    title: str
    position: int | None
    added_at: datetime


class CollectionResponse(CamelModel):
    id: str
    title: str
    description: str | None
    source_tag: str | None
    item_count: int
    entity_types: list[str]
    created_at: datetime
    updated_at: datetime


class CollectionDetailResponse(CollectionResponse):
    items: list[CollectionItemResponse]


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
