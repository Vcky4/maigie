"""
Progress domain â€” API routes.

Analytics, goals, study blocks, streaks, achievements,
spaced repetition, and study sessions.

Mounted at: /api/v1/progress
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query, status

from src.shared.auth import CurrentUser

from . import models
from .repository import progress_repo
from .services import (
    analytics_service,
    goal_insight_service,
    goal_metrics,
    goal_service,
    goal_snapshot_service,
    schedule_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["progress"])


# ===========================================================================
# Study Sessions (Activity)
# ===========================================================================


@router.post("/sessions/start", response_model=models.SessionResponse)
async def start_session(
    current_user: CurrentUser,
    body: models.StartSessionRequest | None = Body(default=None),
):
    """Start a study session."""
    data = body.model_dump() if body else {}
    result = await analytics_service.start_study_session(
        user_id=current_user.id,
        course_id=data.get("courseId"),
        topic_id=data.get("topicId"),
    )
    return models.SessionResponse(
        sessionId=result["sessionId"], startTime=result["startTime"], message=result.get("message")
    )


@router.post("/sessions/{session_id}/stop", response_model=models.SessionResponse)
async def stop_session(session_id: str, current_user: CurrentUser):
    """Stop a study session."""
    result = await analytics_service.stop_study_session(
        session_id=session_id, user_id=current_user.id
    )
    return models.SessionResponse(
        sessionId=result["sessionId"],
        startTime="",
        endTime=result.get("endTime"),
        duration=result.get("duration"),
        message=result.get("message"),
    )


# ===========================================================================
# Streaks
# ===========================================================================


@router.get("/streaks", response_model=models.StreakResponse)
async def get_streak(current_user: CurrentUser):
    """Get current study streak."""
    return await analytics_service.get_streak(user_id=current_user.id)


# ===========================================================================
# Achievements
# ===========================================================================


@router.get("/achievements", response_model=list[models.AchievementResponse])
async def list_achievements(current_user: CurrentUser):
    """Get all unlocked achievements."""
    return await analytics_service.list_achievements(user_id=current_user.id)


# ===========================================================================
# Goals
# ===========================================================================


@router.get("/goals", response_model=models.GoalListResponse)
async def list_goals(
    current_user: CurrentUser,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sortBy: str = Query("createdAt", pattern="^(createdAt|updatedAt|title|targetDate)$"),
    sortOrder: str = Query("desc", pattern="^(asc|desc)$"),
    spaceId: str | None = Query(None),
):
    """List goals with pagination."""
    goals, total = await goal_service.list_goals(
        user_id=current_user.id,
        status=status_filter,
        space_id=spaceId,
        page=page,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    skip = (page - 1) * pageSize
    items = await _goal_responses(goals)
    return models.GoalListResponse(
        goals=items, total=total, page=page, pageSize=pageSize, hasMore=(skip + pageSize) < total
    )


@router.post("/goals", response_model=models.GoalResponse, status_code=201)
async def create_goal(body: models.GoalCreate, current_user: CurrentUser):
    """Create a goal."""
    goal = await goal_service.create_goal(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return (await _goal_responses([goal]))[0]


@router.get("/goals/summary", response_model=models.GoalSummaryResponse)
async def get_goals_summary(
    current_user: CurrentUser,
    momentumWeeks: int = Query(4, ge=1, le=26),
):
    """Everything above the goals list: the portfolio counts, the momentum chart, and what to lead with.

    **Declared before `/goals/{goal_id}`** deliberately: FastAPI matches in declaration order, so the
    other way round this path would arrive as a goal called "summary" and 404.

    The counts come from one query over the whole portfolio. The list endpoint cannot answer them — it is
    paginated, so its twenty rows would give an average that changed as the learner paged.

    **`headline` is a token and the client writes the sentence.** The fixture's hero baked two numbers
    into prose, which is a claim that can disagree with the tiles beneath it; the token says which
    situation applies and the counts beside it supply every figure the sentence needs.

    `momentum` is composed here rather than in a sibling endpoint because it is part of the same header:
    the chart, the tiles and the greeting are one block of the page and are better read in one request
    than assembled from three that could describe different moments.
    """
    portfolio = await goal_metrics.get_goal_portfolio(user_id=current_user.id)
    momentum = await goal_metrics.get_portfolio_momentum(
        user_id=current_user.id, weeks=momentumWeeks
    )
    # Asked of the learner's whole history, not of the window: someone who worked their plan two months
    # ago must not be told completion is "not tracked yet".
    tracked = await goal_metrics.portfolio_completion_ever_recorded(user_id=current_user.id)
    return models.GoalSummaryResponse(
        active=portfolio.active,
        completed=portfolio.completed,
        atRisk=portfolio.at_risk,
        dueSoon=portfolio.due_soon,
        overdue=portfolio.overdue,
        averageProgress=portfolio.average_progress,
        headline=goal_metrics.portfolio_headline(portfolio),
        momentumTracked=tracked,
        momentum=[
            models.GoalMomentumWeek(
                weekStart=week.week_start.isoformat(),
                planned=week.planned,
                completed=week.completed,
            )
            for week in momentum
        ],
    )


@router.get("/goals/{goal_id}", response_model=models.GoalResponse)
async def get_goal(goal_id: str, current_user: CurrentUser):
    """Get a goal."""
    goal = await goal_service.get_goal(goal_id=goal_id, user_id=current_user.id)
    return (await _goal_responses([goal]))[0]


@router.patch("/goals/{goal_id}", response_model=models.GoalResponse)
async def update_goal(goal_id: str, body: models.GoalUpdate, current_user: CurrentUser):
    """Update a goal."""
    goal = await goal_service.update_goal(
        goal_id=goal_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return (await _goal_responses([goal]))[0]


@router.post("/goals/{goal_id}/progress", response_model=models.GoalResponse)
async def record_goal_progress(
    goal_id: str, body: models.GoalProgressUpdate, current_user: CurrentUser
):
    """Record progress on a goal."""
    goal = await goal_service.record_progress(
        goal_id=goal_id, user_id=current_user.id, progress=body.progress
    )
    return (await _goal_responses([goal]))[0]


@router.delete("/goals/{goal_id}", status_code=204)
async def delete_goal(goal_id: str, current_user: CurrentUser):
    """Delete a goal."""
    await goal_service.delete_goal(goal_id=goal_id, user_id=current_user.id)


@router.get("/goals/{goal_id}/history", response_model=models.GoalProgressHistoryResponse)
async def get_goal_history(
    goal_id: str,
    current_user: CurrentUser,
    range: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """A goal's progress trajectory, from `GoalProgressSnapshot`.

    `get_goal` first, so a goal belonging to someone else 404s here exactly as it does everywhere
    else — the history read is scoped by `userId` as well, but ownership is established once, in the
    same place as every other goal route.

    **The window ends yesterday**, matching `growth_service._window` and for the same reason: the
    nightly writer records each learner's most recently *finished* local day, so including today asks
    for a row that by definition does not exist and makes every range report one fewer captured day
    than it has.

    An empty `points` is a legitimate answer, and `firstCapturedOn` is what makes it readable —
    nothing recorded yet, versus history that exists outside this window (Decision Y). Neither is
    filled in with an interpolated line.
    """
    # Ownership. Raises `NotFoundError` for another learner's goal.
    await goal_service.get_goal(goal_id=goal_id, user_id=current_user.id)

    days = {"7d": 7, "30d": 30, "90d": 90}[range]
    until = (datetime.now(UTC) - timedelta(days=1)).date()
    since = until - timedelta(days=days - 1)

    rows = await goal_snapshot_service.list_history(
        user_id=current_user.id, goal_id=goal_id, since=since, until=until
    )
    first = await goal_snapshot_service.first_captured_on(
        user_id=current_user.id, goal_id=goal_id
    )

    return models.GoalProgressHistoryResponse(
        goalId=goal_id,
        days=days,
        capturedDays=len(rows),
        firstCapturedOn=first.isoformat() if first else None,
        points=[
            models.GoalProgressPoint(
                day=row.captured_on.isoformat(),
                progress=row.progress,
                currentValue=row.current_value,
                currentValueMeasured=row.current_value_measured,
                status=row.status,
            )
            for row in rows
        ],
    )


@router.get("/goals/{goal_id}/evidence", response_model=models.GoalEvidenceResponse)
async def get_goal_evidence(
    goal_id: str,
    current_user: CurrentUser,
    limit: int = Query(12, ge=1, le=50),
):
    """Recent dated work behind a goal, newest first.

    A goal is not itself measurable — it points at a course, a topic or a preparation, and the evidence
    is the work done on *that*. `linkedCourseId` publishes which link answered, so a client can say
    "from Linear Algebra" rather than implying the goal recorded this directly.

    **A goal with no link returns an empty list, not the learner's general activity.** Falling back to
    everything they did would attach unrelated work to a goal and make the panel look informative while
    being wrong.

    A preparation-linked goal reads the preparation tables, so `kind` can also be `quiz_session` or
    `practice_answer` here. `ExamPrep` has no join to `Course`, so those are a separate body of evidence
    rather than a view of the course ones.

    Reads the domain tables, not `ActivityFeedEntry` — the feed has no course column and nothing has ever
    tagged an entry with one (§7.2), so a filter there returns nothing for everyone.
    """
    from src.domains.personal_learning.services import reflect_aggregates

    goal = await goal_service.get_goal(goal_id=goal_id, user_id=current_user.id)
    items = await reflect_aggregates.list_goal_evidence(
        user_id=current_user.id, goal=goal, limit=limit
    )

    return models.GoalEvidenceResponse(
        goalId=goal_id,
        linkedCourseId=goal.course_id,
        linkedTopicId=goal.topic_id,
        linkedPrepId=goal.prep_id,
        items=[
            models.GoalEvidenceItem(
                id=item.id,
                kind=item.kind,
                title=item.title,
                detail=item.detail,
                occurredAt=item.occurred_at.isoformat(),
                value=item.value,
                unit=item.unit,
                correct=item.correct,
            )
            for item in items
        ],
    )


@router.get("/goals/{goal_id}/insight", response_model=models.GoalInsightResponse)
async def get_goal_insight(goal_id: str, current_user: CurrentUser):
    """One goal's written interpretation and recommended next action.

    **Its own endpoint rather than fields on `GoalResponse`.** The goals list returns many goals as that
    model, so composing prose there would mean one language model call per goal per page load. Here it
    is one per goal, stored against the figures it was written from.

    **Plus, as a `200` with a `locked` notice** (Decision Z), never a `403`. Every number on the goal
    detail page is free and only the interpretation is paid, so a Free page renders an upgrade card where
    the panel would be rather than an error over figures that are all perfectly fine.

    The recommended action's destination and button text are chosen by the service from what was
    measured (Decision O) and published as a `target` rather than a path, so the client keeps owning its
    route table. `signal` is derived from the same `statusLabel` and `pacePercent` the goal card shows.
    """
    goal = await goal_service.get_goal(goal_id=goal_id, user_id=current_user.id)
    # The assembled response, so the prose is written from the same derived pace and projection the page
    # prints beside it rather than from a second derivation that could differ.
    responses = await _goal_responses([goal])
    return await goal_insight_service.get_goal_insight(
        user_id=current_user.id, goal=goal, goal_response=responses[0]
    )


@router.get("/goals/{goal_id}/momentum", response_model=models.GoalMomentumResponse)
async def get_goal_momentum(
    goal_id: str,
    current_user: CurrentUser,
    weeks: int = Query(4, ge=1, le=26),
):
    """Planned versus completed sessions per week for one goal.

    Reads the goal's `ScheduleBlock` rows — a *goal plan*, which Decision R keeps distinct from a
    `StudyPlan`. Ownership is established by `get_goal` first, as on every other goal route.

    `completionTracked` is what makes a flat-zero `completed` series readable: until a learner marks a
    block done, "you completed nothing" and "nothing is being tracked yet" are indistinguishable from
    the numbers alone, and only one of them is true (Decision Y).
    """
    await goal_service.get_goal(goal_id=goal_id, user_id=current_user.id)

    momentum = await goal_metrics.get_goal_momentum(
        user_id=current_user.id, goal_id=goal_id, weeks=weeks
    )
    # Asked of the goal's whole history, not of the window: a learner who worked through their plan two
    # months ago must not be told completion is "not tracked yet".
    tracked = await goal_metrics.completion_ever_recorded(
        user_id=current_user.id, goal_id=goal_id
    )

    return models.GoalMomentumResponse(
        goalId=goal_id,
        weeks=weeks,
        completionTracked=tracked,
        points=[
            models.GoalMomentumWeek(
                weekStart=week.week_start.isoformat(),
                planned=week.planned,
                completed=week.completed,
            )
            for week in momentum
        ],
    )


@router.get("/goals/{goal_id}/milestones", response_model=list[models.GoalMilestoneResponse])
async def list_goal_milestones(goal_id: str, current_user: CurrentUser):
    """A goal's milestones, in the learner's chosen order.

    A bare list rather than a pagination envelope: milestones are a handful of steps the learner
    typed, bounded by the goal, and the detail page renders all of them. An envelope here would
    publish a `total` nobody reads and a `page` nobody can turn.
    """
    milestones = await goal_service.list_milestones(goal_id=goal_id, user_id=current_user.id)
    return [_to_milestone_response(m) for m in milestones]


@router.post(
    "/goals/{goal_id}/milestones",
    response_model=models.GoalMilestoneResponse,
    status_code=201,
)
async def create_goal_milestone(
    goal_id: str, body: models.GoalMilestoneCreate, current_user: CurrentUser
):
    """Add a milestone to a goal.

    Milestones are rows because they cannot be derived: nothing in the data can infer that a goal
    divides into four stages, and generating a division would assert a structure the learner never
    described.
    """
    milestone = await goal_service.create_milestone(
        goal_id=goal_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_milestone_response(milestone)


@router.patch(
    "/goals/{goal_id}/milestones/{milestone_id}",
    response_model=models.GoalMilestoneResponse,
)
async def update_goal_milestone(
    goal_id: str,
    milestone_id: str,
    body: models.GoalMilestoneUpdate,
    current_user: CurrentUser,
):
    """Edit a milestone, including marking it achieved.

    `achievedAt` is a timestamp on this body rather than a `POST .../achieve` toggle, so a milestone
    reached on Tuesday can be recorded on Thursday with Tuesday's date — and so an explicit `null`
    un-achieves one that was ticked by mistake.
    """
    milestone = await goal_service.update_milestone(
        goal_id=goal_id,
        user_id=current_user.id,
        milestone_id=milestone_id,
        data=body.model_dump(exclude_unset=True),
    )
    return _to_milestone_response(milestone)


@router.delete("/goals/{goal_id}/milestones/{milestone_id}", status_code=204)
async def delete_goal_milestone(goal_id: str, milestone_id: str, current_user: CurrentUser):
    """Remove a milestone."""
    await goal_service.delete_milestone(
        goal_id=goal_id, user_id=current_user.id, milestone_id=milestone_id
    )


@router.post("/goals/{goal_id}/regenerate-plan", response_model=models.GoalRegeneratePlanResponse)
async def regenerate_goal_plan(
    goal_id: str, body: models.GoalRegeneratePlanRequest, current_user: CurrentUser
):
    """Regenerate AI study plan for a goal."""
    result = await goal_service.regenerate_plan(
        user_id=current_user.id,
        goal_id=goal_id,
        duration_weeks=body.duration_weeks,
        request=body.request,
    )
    if result.get("status") != "success":
        code = status.HTTP_429_TOO_MANY_REQUESTS if result.get("rate_limited") else 500
        raise HTTPException(status_code=code, detail=result.get("message", "Failed"))
    return models.GoalRegeneratePlanResponse(
        status="success",
        goal_id=goal_id,
        deleted_schedule_blocks=result.get("deleted_schedule_blocks", 0),
        created_schedule_blocks=result.get("created_schedule_blocks", 0),
        target_date=result.get("target_date"),
        study_tips=result.get("study_tips", []),
        message=result.get("message"),
    )


# ===========================================================================
# Study Blocks (Schedule)
# ===========================================================================


@router.get("/schedule", response_model=models.StudyBlockListResponse)
async def list_schedule(
    current_user: CurrentUser,
    startDate: datetime | None = Query(None),
    endDate: datetime | None = Query(None),
    courseId: str | None = Query(None),
    goalId: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    """List study blocks (schedule) with date range filter."""
    blocks, total = await schedule_service.list_blocks(
        user_id=current_user.id,
        start_date=startDate,
        end_date=endDate,
        course_id=courseId,
        goal_id=goalId,
        page=page,
        page_size=pageSize,
    )
    skip = (page - 1) * pageSize
    items = [_to_block_response(b) for b in blocks]
    return models.StudyBlockListResponse(
        schedules=items,
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(skip + pageSize) < total,
    )


@router.post("/schedule", response_model=models.StudyBlockResponse, status_code=201)
async def create_block(body: models.StudyBlockCreate, current_user: CurrentUser):
    """Create a study block."""
    block = await schedule_service.create_block(user_id=current_user.id, data=body.model_dump())
    return _to_block_response(block)


@router.put("/schedule/{block_id}", response_model=models.StudyBlockResponse)
async def update_block(block_id: str, body: models.StudyBlockUpdate, current_user: CurrentUser):
    """Update a study block."""
    block = await schedule_service.update_block(
        block_id=block_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_block_response(block)


@router.delete("/schedule/{block_id}", status_code=204)
async def delete_block(block_id: str, current_user: CurrentUser):
    """Delete a study block."""
    await schedule_service.delete_block(block_id=block_id, user_id=current_user.id)


# ===========================================================================
# Spaced Repetition (Review Schedules)
# ===========================================================================


@router.get("/reviews/due", response_model=list[models.ReviewScheduleResponse])
async def list_due_reviews(current_user: CurrentUser):
    """Get topics due for review (spaced repetition)."""
    reviews = await progress_repo.list_due_reviews(current_user.id, before=datetime.now(UTC))
    return [
        models.ReviewScheduleResponse(
            id=r.id,
            userId=r.user_id,
            topicId=r.topic_id,
            topicTitle=r.topic.title if r.topic else None,
            nextReviewAt=r.next_review_at,
            intervalDays=r.interval_days,
            repetitionCount=r.repetition_count,
            easeFactor=r.ease_factor,
            lastQuality=r.last_quality,
            lapseCount=r.lapse_count,
            lastReviewedAt=r.last_reviewed_at,
        )
        for r in reviews
    ]


@router.post("/reviews/{review_id}/submit")
async def submit_review(
    review_id: str, body: models.ReviewQualityRequest, current_user: CurrentUser
):
    """Submit review quality for spaced repetition (SM-2 algorithm)."""
    from src.domains.progress.services.spaced_repetition_impl import advance_review_sqlalchemy

    result = await advance_review_sqlalchemy(current_user.id, review_id, body.quality)
    return result


# ===========================================================================
# Helpers
# ===========================================================================


def _to_goal_response(
    goal,
    *,
    measurement: goal_metrics.GoalMeasurement | None = None,
    milestones: tuple[int, int] = (0, 0),
    now: datetime | None = None,
) -> models.GoalResponse:
    """Map a goal row onto the wire, deriving everything the row does not store.

    `measurement` and `milestones` are passed in rather than fetched here, because a list of twenty
    goals must not turn into twenty round trips per derived field. Callers batch them; a caller with
    nothing to pass gets `currentValue` from the row, which is correct for a `manual` goal and null
    for the rest — honest in both cases, and never a fabricated zero.
    """
    moment = now or datetime.now(UTC)
    progress = goal.progress or 0.0
    achieved, total = milestones

    return models.GoalResponse(
        id=goal.id,
        userId=goal.user_id,
        title=goal.title,
        description=goal.description,
        targetDate=goal.target_date.isoformat() if goal.target_date else None,
        status=goal.status,
        progress=progress,
        courseId=goal.course_id,
        topicId=goal.topic_id,
        prepId=goal.prep_id,
        metricKind=goal.metric_kind,
        targetValue=goal.target_value,
        unit=goal.unit,
        currentValue=(measurement.current_value if measurement else goal.current_value),
        currentValueMeasured=(measurement.measured if measurement else False),
        statusLabel=goal_metrics.status_label(
            progress=progress,
            status=goal.status,
            created_at=goal.created_at,
            target_date=goal.target_date,
            now=moment,
        ),
        pacePercent=goal_metrics.pace_percent(
            progress=progress,
            created_at=goal.created_at,
            target_date=goal.target_date,
            now=moment,
        ),
        projectedOutcome=goal_metrics.projected_outcome(
            progress=progress,
            created_at=goal.created_at,
            target_date=goal.target_date,
            now=moment,
        ),
        milestonesTotal=total,
        milestonesAchieved=achieved,
        createdAt=goal.created_at.isoformat(),
        updatedAt=goal.updated_at.isoformat(),
    )


def _to_milestone_response(milestone) -> models.GoalMilestoneResponse:
    return models.GoalMilestoneResponse(
        id=milestone.id,
        goalId=milestone.goal_id,
        title=milestone.title,
        detail=milestone.detail,
        targetValue=milestone.target_value,
        orderIndex=milestone.order_index,
        achievedAt=milestone.achieved_at.isoformat() if milestone.achieved_at else None,
        createdAt=milestone.created_at.isoformat(),
        updatedAt=milestone.updated_at.isoformat(),
    )


async def _goal_responses(goals: list, *, now: datetime | None = None) -> list[models.GoalResponse]:
    """Map several goals, batching the two derived reads across the whole set.

    Two queries' worth of work for any number of goals — `derive_current_values` issues one per
    metric kind present and `count_achieved_milestones` one in total — rather than per goal.
    """
    if not goals:
        return []
    moment = now or datetime.now(UTC)
    measurements = await goal_metrics.derive_current_values(goals, now=moment)
    milestone_counts = await goal_metrics.count_achieved_milestones([goal.id for goal in goals])
    return [
        _to_goal_response(
            goal,
            measurement=measurements.get(goal.id),
            milestones=milestone_counts.get(goal.id, (0, 0)),
            now=moment,
        )
        for goal in goals
    ]


def _to_block_response(block) -> models.StudyBlockResponse:
    return models.StudyBlockResponse(
        id=block.id,
        userId=block.user_id,
        title=block.title,
        description=block.description,
        startAt=block.start_at.isoformat(),
        endAt=block.end_at.isoformat(),
        recurringRule=block.recurring_rule,
        courseId=block.course_id,
        topicId=block.topic_id,
        goalId=block.goal_id,
        reviewItemId=block.review_item_id,
        googleCalendarEventId=block.google_calendar_event_id,
        googleCalendarSyncedAt=(
            block.google_calendar_synced_at.isoformat() if block.google_calendar_synced_at else None
        ),
        completedAt=block.completed_at.isoformat() if block.completed_at else None,
        createdAt=block.created_at.isoformat(),
        updatedAt=block.updated_at.isoformat(),
    )
