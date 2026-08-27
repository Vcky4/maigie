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
    #: Who owns the deadline: `external` when the goal is attached to a preparation, whose date is set
    #: by whoever runs the exam, `learner` otherwise. Derived from the link, never stored.
    #:
    #: It tells a client what falling behind can be *answered* with. An external deadline cannot be
    #: moved — the exam is on the 15th — so the only honest options are compressing the plan or asking
    #: the learner how it went. The learner's own date can simply be rescheduled.
    #:
    #: A `str` rather than an enum, matching `statusLabel` and `metricKind`, so a third authority can be
    #: added later without it being a breaking change for either client.
    dateAuthority: str = "learner"
    #: How many times this goal's deadline has been pushed **later**, from `GoalScheduleChange`.
    #:
    #: Published because a rewritten deadline is otherwise invisible and flattering: `pacePercent` and
    #: `projectedOutcome` both measure against `createdAt → targetDate`, so every extension enlarges the
    #: window and improves the numbers. A goal extended three times reads as comfortable. This is the
    #: field that lets a surface say otherwise.
    #:
    #: Counts extensions only. Pulling a deadline earlier, or setting a first one on a goal that had
    #: none, is a schedule change but not extra room, and is excluded. Reads `0` for every goal whose
    #: deadline has not moved since this began being recorded — history before then does not exist.
    extendedCount: int = 0
    #: The deadline this goal started with, when its deadline has moved. `null` when it has not.
    #:
    #: Alongside `extendedCount` rather than instead of it: "moved twice" and "was originally due in
    #: August" are different sentences and a surface may want either. Not applied to `pacePercent`,
    #: which still measures against the current deadline — re-basing that window is a behaviour change
    #: with its own decision to make.
    originalTargetDate: str | None = None
    milestonesTotal: int = 0
    milestonesAchieved: int = 0

    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class GoalMomentumWeek(BaseModel):
    """One week of a goal's plan."""

    #: The Monday of the week, `YYYY-MM-DD`.
    weekStart: str
    #: Blocks scheduled for that week.
    planned: int
    #: Blocks the learner marked done. Reads `0` until learners start marking them — the column that
    #: records it was added for this chart, because completion was previously stored nowhere.
    completed: int


class GoalSummaryResponse(BaseModel):
    """The counts above the goals list, over the **whole** portfolio.

    Its own endpoint rather than fields on `GoalListResponse`, because the list is paginated and
    these are not. Folding them into the list envelope would mean either recomputing the portfolio on
    every page request or publishing counts that quietly described only page one — and the second is
    the kind of number that looks right until a learner has twenty-one goals.
    """

    active: int
    completed: int
    #: Active goals that have fallen further behind their own schedule than `AT_RISK_LAG_POINTS`.
    atRisk: int
    #: Active goals whose deadline is inside the next `DUE_SOON_DAYS` and still ahead.
    dueSoon: int
    #: Active, unfinished goals whose deadline has already passed. Separate from `dueSoon` so a goal
    #: three weeks late cannot be reported as "due this week".
    overdue: int
    #: Mean progress across active and completed goals. `null` when the learner has none, never `0` —
    #: no goals is not the same as no progress. Archived and cancelled goals are excluded.
    averageProgress: float | None = None
    #: Which situation the page should lead with: `none` | `overdue` | `at_risk` | `due_soon` |
    #: `all_complete` | `strong` | `steady`.
    #:
    #: **A token, not a sentence.** The fixture's hero baked two numbers into prose — "You have 4 active
    #: goals with an average progress of 58%…" — which is a claim that can disagree with the tiles
    #: beneath it. The counts above and this token are enough for a client to write the sentence from the
    #: same data it renders, and the wording then belongs to the client, which is right because a mobile
    #: hero and a web hero want different lengths.
    #:
    #: The *choice* of which fact leads is still the server's, for the reason Decision O gives about
    #: action targets: it is a judgement about the learner's data, and two clients making it
    #: independently would eventually disagree about whether an overdue goal outranks a slipping one.
    headline: str = "steady"
    #: Planned versus completed sessions per week across every goal, oldest week first, for the chart
    #: above the list. Counts only blocks attached to a goal, so this cannot exceed the sum of the
    #: per-goal charts.
    momentum: list[GoalMomentumWeek] = Field(default_factory=list)
    #: `true` once any goal-plan block has **ever** been marked done, asked of the learner's whole
    #: history. Lets the client caption a flat-zero `completed` series "not tracked yet" rather than
    #: "you completed nothing" (Decision Y).
    momentumTracked: bool = False


class GoalEvidenceItem(BaseModel):
    """One dated thing the learner did that counts towards a goal.

    Mirrors `personal_learning.models.EvidenceItem` in a plain `BaseModel` because this domain does not
    use `CamelModel` — the field names are the wire names here. The shape is deliberately identical so a
    client can render subject evidence and goal evidence with one component.
    """

    id: str
    #: A course- or topic-linked goal yields `topic_completed` | `section_completed` | `study_session` |
    #: `knowledge_check`. A preparation-linked one yields `quiz_session` | `practice_answer`, which come
    #: from different tables entirely — `ExamPrep` has no join to `Course` (§7.2). This is the only
    #: endpoint that can publish all six, which is why it stays a `str` rather than mirroring the
    #: narrower Literal on the subject-detail response.
    kind: str
    title: str
    detail: str | None = None
    occurredAt: str
    #: Numeric rather than pre-formatted, so it cannot disagree with the figure beside it. A completed
    #: quiz publishes its score here; a `practice_answer` has no figure and reports `correct` instead.
    value: float | None = None
    unit: str | None = None
    #: Only meaningful for `knowledge_check` and `practice_answer`. A study session or a quiz score is
    #: not correct or incorrect.
    correct: bool | None = None


class GoalEvidenceResponse(BaseModel):
    """The work behind a goal, and which of its links produced it."""

    goalId: str
    #: Which link answered. Published so a client can say "from Linear Algebra" rather than implying the
    #: goal recorded this work directly.
    linkedCourseId: str | None = None
    linkedTopicId: str | None = None
    #: Present for a `prep_readiness` goal, but **prep evidence is not yet read** — `ExamPrep` has no
    #: join to `Course` anywhere, so it needs its own reader over the prep tables. Published so the gap
    #: is visible rather than looking like a goal with no activity.
    linkedPrepId: str | None = None
    items: list[GoalEvidenceItem] = []


#: How this goal is travelling, as a token rather than display copy. Derived from `statusLabel` and
#: `pacePercent` — both already published on `GoalResponse` — so the badge beside the insight cannot
#: disagree with the badge on the goal card. `not_paced` is a real state: a goal with no target date has
#: no schedule to be ahead or behind of, and calling that "on track" would be an unmeasured claim.
GoalInsightSignal = Literal["achieved", "ahead", "on_track", "behind", "not_paced"]


class LockedNoticeResponse(BaseModel):
    """Why a panel is unavailable on this plan, in the shape the upsell card already renders.

    Mirrors `personal_learning.models.LockedNotice` for the same reason `GoalNextActionTarget` mirrors
    its counterpart: one client component renders both, and this domain serialises with literal
    camelCase names rather than through `CamelModel`.
    """

    locked: bool = True
    reason: str
    capability: str
    upgradeUrl: str = "/subscription"
    trialAvailable: bool = False
    upgradeValue: str = ""


class GoalInsight(BaseModel):
    """The written interpretation of one goal's figures.

    **Plus, delivered as a `200` with a `locked` notice** (Decision Z). `title` is a heading and is
    trimmed; `detail` is a sentence and is `null` when the model did not finish one, because a fragment
    beside a progress ring reads as a finding rather than as a gap.

    `signal` is computed, not written. It is a classification of measured pace, which is arithmetic.
    """

    title: str
    detail: str | None = None
    signal: GoalInsightSignal


class GoalNextActionTarget(BaseModel):
    """Where a recommended action points, as data rather than as a path.

    Mirrors `personal_learning.models.ReflectionActionTarget` field for field, deliberately: the web
    client already turns that shape into a URL in one place, and a second shape would mean a second
    route table free to drift from the first. Restated here rather than imported because this domain's
    models are plain `BaseModel` with literal camelCase names, and importing a `CamelModel` would give
    one response two serialisation conventions.

    `kind` is the same closed vocabulary — `preparation_practice`, `study_plan`, `schedule`,
    `flashcard_review`, `course`, `note`, `goal`, `none`. A backend that emitted `/prepare/x/practice`
    would own the web client's routing and be wrong on every other client.
    """

    kind: str = "none"
    entityId: str | None = None
    #: Practice mode, meaningful only for the practice kinds.
    mode: str | None = None


class GoalNextAction(BaseModel):
    """The recommended next move on a goal.

    **`target` and `label` are both chosen by the service** (Decision O). The target because a model
    free to name an entity would eventually cite one this learner does not own; the label because a
    model-written button caption over a service-chosen destination is a button that lies about where it
    goes. `label` is `""` for a `none` target, where the card renders without a button at all.
    """

    title: str
    detail: str | None = None
    label: str = ""
    target: GoalNextActionTarget = Field(default_factory=GoalNextActionTarget)


class GoalInsightResponse(BaseModel):
    """The insight and next-action panels on the goal detail page.

    **Its own endpoint rather than fields on `GoalResponse`.** The goals list returns many goals and
    `GoalResponse` is what it returns them as; composing prose there would mean one language model call
    per goal per page load. Here it is one call per goal, cached against the figures it was written
    from, and the detail page's numbers paint without waiting for it.
    """

    goalId: str
    #: Present for a learner on Free, with `insight` and `nextAction` both null.
    locked: LockedNoticeResponse | None = None
    #: `null` for Free, and also when composition failed. The page renders an absent panel, which it
    #: already handles, rather than an error over figures that are all perfectly fine.
    insight: GoalInsight | None = None
    nextAction: GoalNextAction | None = None


class GoalMomentumResponse(BaseModel):
    """Planned versus completed sessions per week, oldest week first.

    **Weeks with nothing planned are included at zero**, unlike the activity feed's daily counts where
    a missing day is omitted. The difference is what the absence means: there, no row means nothing was
    recorded; here, a week the learner scheduled nothing is itself part of the answer to "did the plan
    get done".
    """

    goalId: str
    weeks: int
    #: `true` once any block for this goal has **ever** been marked done — asked of the goal's whole
    #: history, not just the requested weeks. Lets the client caption a flat-zero `completed` series as
    #: "not tracked yet" rather than "you completed nothing", which are very different messages to put
    #: in front of a learner (Decision Y). Scoped to the window it would have answered the wrong
    #: question for anyone who worked their plan and then paused.
    completionTracked: bool = False
    points: list[GoalMomentumWeek] = []


class GoalProgressPoint(BaseModel):
    """One recorded day for one goal."""

    #: `YYYY-MM-DD`, the learner's own calendar day.
    day: str
    progress: float
    #: The measured figure behind the percentage. `null` when the goal's kind had no source to
    #: measure on that day — never `0`, which would claim no progress (Decision I).
    currentValue: float | None = None
    #: `true` when `currentValue` came from event rows rather than from the learner.
    currentValueMeasured: bool = False
    #: The goal's lifecycle value on that day, so a chart can show where it completed rather than
    #: simply stopping.
    status: str = "ACTIVE"


class GoalProgressHistoryResponse(BaseModel):
    """A goal's trajectory, and an honest account of how much of one exists yet.

    **`points` may legitimately be empty, and that means two different things** — hence
    `firstCapturedOn`. A goal created this morning has no history because nothing has been recorded
    yet; a goal whose window predates the feature has none because the table did not exist. Both are
    "building", and neither is a flat line at today's value, which is what interpolating would have
    drawn (Decision Y).

    There is no `reconstructed` flag as `GrowthTrendPoint` carries, because there is nothing to
    reconstruct from: `Goal.progress` is overwritten in place with no dated event trail. Every point
    here was observed on the day it is dated.
    """

    goalId: str
    #: The requested window in days.
    days: int
    #: Days inside the window that actually have a row. `0` with a non-null `firstCapturedOn` means
    #: the goal has history, just not in this window.
    capturedDays: int
    #: The earliest day ever recorded for this goal, or `null` if none is. Lets the client say
    #: "building since 23 August" rather than showing an empty chart with no explanation.
    firstCapturedOn: str | None = None
    points: list[GoalProgressPoint] = []


class GoalListResponse(BaseModel):
    goals: list[GoalResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class DerivableGoalResponse(BaseModel):
    """A goal the learner's stated intent implies, before anything is written.

    Returned by the preview endpoint so a client can ask before creating. `basis` is a TOKEN
    (`course` | `preparation`) naming what the goal was read from; the client writes the sentence.
    """

    title: str
    description: str
    metricKind: str
    unit: str
    basis: str
    courseId: str | None = None
    prepId: str | None = None
    #: Absent when the learner never stated one. Not guessed — see `goal_derivation_service`.
    targetValue: float | None = None
    targetDate: datetime | None = None


class DerivableGoalsResponse(BaseModel):
    """Empty `goals` means every piece of stated intent already has a goal, which is the ordinary
    answer once derivation has run once."""

    goals: list[DerivableGoalResponse] = []


class DerivedGoalsResponse(BaseModel):
    goals: list[GoalResponse] = []
    created: int = 0


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
    #: Marking a planned session done, or undoing it with an explicit `null`.
    #:
    #: There is no separate `/complete` route: `update_block` already distinguishes an explicit null
    #: from an omitted key, so one field on the existing `PUT` gives both directions. A timestamp
    #: rather than a boolean because a Tuesday session can be marked done on Thursday and should keep
    #: Tuesday's date — the learner's own clock is the right authority for when they studied.
    completedAt: datetime | None = None


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
    #: When the learner recorded this block as done. `null` means not done, rather than unknown — a
    #: block is only ever completed by an explicit action.
    completedAt: str | None = None
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


class AgendaAcceptRequest(BaseModel):
    """Accept a suggested placement, turning it into a scheduled block.

    `entryId` is the agenda entry's own namespaced id (`plan_item:…`, `review:…`), which is what tells the
    server what the block should be linked to. `startAt` and `minutes` are sent rather than assumed, so a
    learner who nudged the suggestion to a different hour gets the hour they chose.
    """

    entryId: str
    startAt: datetime
    minutes: int = Field(ge=5, le=480)
    #: Optional override. Defaults to the title the agenda proposed.
    title: str | None = Field(default=None, max_length=200)


class AgendaEntryResponse(BaseModel):
    """One thing on the learner's day, from whichever store schedules it.

    `startAt`/`endAt` are always present so a client can lay a day out, but they only *mean* a clock
    reading when `timed` is true:

    - `timed: true` — a schedule block or a live class. Someone put it at that hour.
    - `timed: false` with `placement: preferred_window | default_window` — day-scoped work that this
      response is *suggesting* a time for, around what is already fixed. Nothing is stored; accepting the
      suggestion is a separate call, and until then the learner owes it nothing.
    - `timed: false` with `placement: no_room` — the day had no gap long enough. The work stays on its day
      with no suggested clock, rather than being crammed into a slot it does not fit.

    `placement` and `window` are **tokens, not copy**. The service decides where the work goes and why; the
    client writes the sentence — the same split the goals greeting and the growth driver impacts use.
    """

    id: str
    #: `schedule` | `study_plan` | `review` | `space_session`.
    source: str
    title: str
    detail: str | None = None
    startAt: str
    endAt: str
    minutes: int
    timed: bool
    #: `fixed` | `preferred_window` | `default_window` | `no_room`.
    placement: str
    #: `morning` | `afternoon` | `evening` | `night`, for a placed entry. `None` otherwise.
    window: str | None = None
    completed: bool = False
    #: How many underlying items this entry stands for — cards in a review batch. `None` when it is one.
    count: int | None = None
    #: Whatever the source knows the work is attached to, so a client can route from the row. Keys vary by
    #: `source`, which is why this is a free mapping rather than a fixed set of nullable columns.
    links: dict = {}


class AgendaResponse(BaseModel):
    """The learner's agenda across a window, and what the placements were based on.

    `placementBasis` is published because it changes what a client may claim. `learner` means the windows
    came from this learner's own recorded study times; `default` means nothing is known yet and the work
    was placed in neutral daytime hours — so the page can offer to learn rather than implying it already
    has.
    """

    from_: str = Field(alias="from")
    to: str
    entries: list[AgendaEntryResponse]
    #: `learner` | `default`.
    placementBasis: str = "default"
    #: Counts by source, so a client can caption the day without re-deriving them from the list.
    totals: dict = {}

    model_config = {"populate_by_name": True}


class CalendarStatusResponse(BaseModel):
    """Whether this learner's Google Calendar is connected, and whether it still works.

    **Carries no token and no token fragment.** A client needs to know the state of the connection, not
    the credential behind it.

    `needsReconnect` is separate from `connected` on purpose. A stored access token that has expired with
    no refresh token alongside it cannot be renewed, so the connection exists and is useless. Reporting
    that as "not connected" would send the learner through a flow that appears to succeed and changes
    nothing; reporting it as connected would leave them waiting for events that never arrive.
    """

    connected: bool = False
    syncEnabled: bool = False
    #: The dedicated "Maigie Schedule" calendar, created on connect. `None` before the first connect.
    calendarId: str | None = None
    expiresAt: str | None = None
    needsReconnect: bool = False


class CalendarConnectResponse(BaseModel):
    """Where to send the learner to grant calendar access.

    `state` is returned so the client can store it and compare it when the learner comes back, which is
    what makes the callback resistant to being forged. The server encodes its own copy inside the value.
    """

    authorizationUrl: str
    state: str


class CalendarDisconnectResponse(BaseModel):
    """The result of forgetting the learner's calendar credentials."""

    disconnected: bool = True
    #: Events already written to Google are left alone — see `GoogleCalendarService.disconnect`.
    message: str


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
