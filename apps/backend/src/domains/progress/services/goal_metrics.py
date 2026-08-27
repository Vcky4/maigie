"""What a goal measures, and everything derived from it.

Owns three things the Reflect goal surfaces render and `Goal` does not store: the learner's
**current value** against their target, the **pace** that implies, and the three-way **status**
label.

**Why the arithmetic lives in `progress` rather than beside the Reflect code that reads it.**
`Goal` is a `progress` entity, and Decision N asks for one at-risk threshold used by both
`/progress/goals` and `/reflect/goals`. Defining it in `personal_learning` would mean the domain
that owns goals importing a rule about goals from a domain that merely reads them — and, in
practice, a second copy appearing the first time somebody needed it here.
`reflect_aggregates` imports `AT_RISK_LAG_POINTS` and `is_at_risk` from this module.

**`currentValue` is derived for every `metricKind` except `manual`, and that is the point of the
discriminator.** "Study 300 focused minutes" is measurable from `StudySession` and must never be a
number the learner typed; a goal with no measurable source is `manual` and says so. Storing a
derived figure would create a second version of something that already exists, and it would start
disagreeing the moment the source moved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select

from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

#: How far a goal may fall behind its own elapsed time before it is called at risk, in percentage
#: points. One threshold, defined once, read by every surface that labels a goal — the alternative
#: is `/goals` and `/reflect/goals` disagreeing about the same goal in front of the same learner.
AT_RISK_LAG_POINTS = 15.0

#: How near a deadline has to be for a goal to count as "due soon", in days.
#:
#: Seven, because the design's tile reads "Due this week". Defined here for the same reason
#: `AT_RISK_LAG_POINTS` is: `/goals` and `/reflect/goals` both print a deadline count, and two
#: definitions of "this week" would have them disagree about the same goal on the same screen.
DUE_SOON_DAYS = 7

MetricKind = Literal[
    "focused_minutes",
    "topics_mastered",
    "cards_reviewed",
    "course_progress",
    "prep_readiness",
    "manual",
]

#: The closed set, matching the `Goal_metricKind_check` constraint. Duplicated deliberately as a
#: tuple for validation and iteration; a test pins the two against each other, because a value
#: accepted by Pydantic and refused by Postgres is a 500 rather than a 422.
METRIC_KINDS: tuple[MetricKind, ...] = (
    "focused_minutes",
    "topics_mastered",
    "cards_reviewed",
    "course_progress",
    "prep_readiness",
    "manual",
)

#: Kinds whose `currentValue` is measured from event rows accumulated **since the goal was
#: created**. A goal is a commitment made at a moment, so work done before it existed did not go
#: towards it.
_ACCUMULATING_KINDS = frozenset({"focused_minutes", "topics_mastered", "cards_reviewed"})

#: Kinds whose `currentValue` is a **current state** rather than an accumulation.
_STATE_KINDS = frozenset({"course_progress", "prep_readiness"})

GoalStatusLabel = Literal["COMPLETED", "ON_TRACK", "NEEDS_ATTENTION"]

#: Who owns a goal's deadline.
#:
#: `external` — the date was set by the world and cannot be moved. An exam is on the 15th whatever
#: the learner's plan says. `learner` — the date was always an intention, and moving it is a
#: legitimate answer to falling behind.
#:
#: The distinction decides what falling behind *means*: an external deadline can only be answered by
#: compressing the plan or asking the learner how it went, while the learner's own date can be
#: rescheduled and the effort kept steady.
DateAuthority = Literal["external", "learner"]


@dataclass(frozen=True)
class GoalPortfolio:
    """The counts above the goals list.

    **Moved here from `personal_learning.services.reflect_aggregates`**, which re-exports it. `Goal`
    is a `progress` entity and this is arithmetic over its rows, so it belongs beside the at-risk
    threshold it depends on. The move also lets `progress`'s own summary route read it without
    importing `personal_learning`, which would have closed an import cycle — `reflect_aggregates`
    already imports this module.
    """

    active: int
    completed: int
    at_risk: int
    #: Active goals whose deadline falls inside the next `DUE_SOON_DAYS`.
    due_soon: int
    #: Active, unfinished goals whose deadline has already passed.
    overdue: int
    #: Mean progress across active and completed goals, or `None` when there are none.
    #: Archived and cancelled goals are excluded: they are goals the learner stopped, and averaging
    #: them in would make abandoning a goal look like a drop in performance.
    average_progress: float | None


# ---------------------------------------------------------------------------
# Pace and status — pure
# ---------------------------------------------------------------------------


def _utc(value: datetime | None) -> datetime | None:
    """A naive datetime read as UTC, so the comparisons below cannot raise.

    **`Goal.targetDate` and `Goal.createdAt` are `timestamp without time zone` in the database**, while
    the ORM declares them `DateTime(timezone=True)`. asyncpg honours the database, so both arrive naive,
    and every predicate here compares them against an aware `datetime.now(UTC)`. That combination raises
    `TypeError: can't compare offset-naive and offset-aware datetimes` — which meant `GET /progress/goals`
    returned a `500` for any goal that had a target date at all.

    Normalised here rather than at each call site because there are four predicates and they must agree:
    a goal counted overdue by one and not-at-risk by another would publish two contradictory labels for
    the same deadline. UTC rather than the learner's zone because the column stores no offset to
    interpret, and inventing one would move deadlines by hours; a deadline is a date the learner set, and
    reading it as UTC is the assumption the rest of this table's writers already make.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def elapsed_percent(
    *, created_at: datetime | None, target_date: datetime | None, now: datetime
) -> float | None:
    """How far through its own window a goal is, 0-100, or `None` when it has no window.

    `None` rather than `0` for an open-ended goal. A goal with no deadline is not at the start of
    anything, and every consumer here has to treat "no schedule" differently from "no progress".
    """
    created_at, target_date, now = _utc(created_at), _utc(target_date), _utc(now)
    if created_at is None or target_date is None:
        return None
    total = (target_date - created_at).total_seconds()
    if total <= 0:
        return None
    return max(0.0, (now - created_at).total_seconds() / total * 100)


def is_at_risk(
    *,
    progress: float,
    created_at: datetime | None,
    target_date: datetime | None,
    now: datetime,
) -> bool:
    """Whether a goal has fallen behind the pace its own deadline implies.

    Pure, because this is the arithmetic that decides a label the learner sees and it should be
    testable without a database.

    A goal with no `targetDate` is **never** at risk. There is no pace to fall behind when there is
    no deadline, and calling an open-ended goal "needs attention" for making slow progress would be
    inventing a commitment the learner never made.

    A goal already past its deadline and unfinished is at risk regardless of progress.
    """
    created_at, target_date, now = _utc(created_at), _utc(target_date), _utc(now)
    if target_date is None or created_at is None:
        return False
    if progress >= 100:
        return False
    if now >= target_date:
        return True

    elapsed = elapsed_percent(created_at=created_at, target_date=target_date, now=now)
    if elapsed is None:
        return False
    return (elapsed - progress) > AT_RISK_LAG_POINTS


def date_authority(goal: Any) -> DateAuthority:
    """Who owns this goal's deadline: the world, or the learner.

    **Derived from the link, never stored.** A goal attached to a preparation takes its date from
    `ExamPrep.examDate`, which is `NOT NULL` and set by whoever runs the exam; every other goal's date
    came from the learner, either from the nullable `Course.targetDate` or from nothing at all. Storing
    the answer would create a second field that can disagree with the link it was computed from, which
    is the mistake `Goal.progress` already is — a column nothing writes, shadowing a figure that is
    always measured on read.

    A goal with no `targetDate` is still `learner` authority. There is nothing to own yet, and the
    alternative — a third token for "no deadline" — would make every caller handle a case that behaves
    exactly like the learner's own date does: freely settable.

    `prep_id` rather than `metric_kind`. The four links are independent nullable columns and nothing
    enforces that a `prep_readiness` goal carries a `prepId`, so reading the kind would call a goal
    external on the strength of a label while the row holds no exam date to be external *to*.
    """
    return "external" if getattr(goal, "prep_id", None) else "learner"


def is_due_soon(
    *,
    status: str,
    progress: float,
    target_date: datetime | None,
    now: datetime,
) -> bool:
    """Whether a deadline is inside the next `DUE_SOON_DAYS` and still ahead.

    Deliberately excludes a deadline already passed — that is `is_overdue`, and folding the two
    together would let a goal three weeks late be reported as "due this week", which is the one
    reading of the tile that would make a learner relax.

    Finished and abandoned goals have no deadline pressure, so only `ACTIVE` goals qualify. A goal at
    100% that nobody marked complete is excluded too: the work is done, and chasing it would be
    telling the learner to do something they have already done.
    """
    target_date, now = _utc(target_date), _utc(now)
    if target_date is None or status != "ACTIVE" or progress >= 100:
        return False
    if now >= target_date:
        return False
    return (target_date - now) <= timedelta(days=DUE_SOON_DAYS)


def is_overdue(
    *,
    status: str,
    progress: float,
    target_date: datetime | None,
    now: datetime,
) -> bool:
    """Whether an unfinished active goal's deadline has already passed.

    Published alongside `dueSoon` rather than merged into it. A learner with two goals due this week
    and one a month overdue is in a different situation from one with three due this week, and a
    single count cannot say which.
    """
    target_date, now = _utc(target_date), _utc(now)
    if target_date is None or status != "ACTIVE" or progress >= 100:
        return False
    return now >= target_date


def status_label(
    *,
    progress: float,
    status: str,
    created_at: datetime | None,
    target_date: datetime | None,
    now: datetime,
) -> GoalStatusLabel:
    """The three states the design renders, derived rather than stored.

    Derived because two of the three are questions about *today*: a stored `ON_TRACK` is wrong by
    tomorrow morning. `Goal.status` remains the learner's own lifecycle value (`ACTIVE`,
    `COMPLETED`, `ARCHIVED`) and is not replaced by this.
    """
    if status == "COMPLETED" or progress >= 100:
        return "COMPLETED"
    if is_at_risk(progress=progress, created_at=created_at, target_date=target_date, now=now):
        return "NEEDS_ATTENTION"
    return "ON_TRACK"


def pace_percent(
    *,
    progress: float,
    created_at: datetime | None,
    target_date: datetime | None,
    now: datetime,
) -> float | None:
    """Progress as a share of where the schedule says it should be. 100 means exactly on pace.

    `None` when the goal has no window, because there is no pace without a deadline. Also `None`
    at the very start of a window, where dividing by an elapsed fraction near zero produces a
    number that swings wildly and means nothing.
    """
    elapsed = elapsed_percent(created_at=created_at, target_date=target_date, now=now)
    if elapsed is None or elapsed < 1.0:
        return None
    return round(progress / elapsed * 100, 1)


def projected_outcome(
    *,
    progress: float,
    created_at: datetime | None,
    target_date: datetime | None,
    now: datetime,
) -> float | None:
    """Where this goal lands by its deadline at the current rate, as a percentage.

    A straight-line extrapolation, and capped at 100 because a goal cannot be more than finished.
    `None` without a deadline, and `None` before enough of the window has elapsed for a rate to
    mean anything — the same 1% floor `pace_percent` uses, so the two are never inconsistent.
    """
    elapsed = elapsed_percent(created_at=created_at, target_date=target_date, now=now)
    if elapsed is None or elapsed < 1.0:
        return None
    return round(min(progress / elapsed * 100, 100.0), 1)


# ---------------------------------------------------------------------------
# Current value — measured
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalMeasurement:
    """What a goal's metric currently reads, and whether anything measured it."""

    current_value: float | None
    #: `True` when this came from event rows or a stored aggregate, `False` for a `manual` goal
    #: where the learner supplied it. The response publishes it so a client never has to guess
    #: which of the two it is holding.
    measured: bool


async def derive_current_values(
    goals: list[Any], *, now: datetime | None = None
) -> dict[str, GoalMeasurement]:
    """Measure every goal's `currentValue`, in a fixed number of queries.

    **One query per metric kind present, not one per goal.** A goal list is up to twenty rows, and
    a query each would be twenty round trips to fill one column — the same mistake the Reflect
    backfill made at ninety times the scale before it was rewritten. Event rows are fetched once
    for the whole set from the earliest goal's creation date and attributed in memory.

    `manual` goals return their stored value with `measured=False`. Every other kind returns a
    measured figure, or `None` when the goal lacks the link its kind needs — a `course_progress`
    goal with no `courseId` has nothing to measure, and `None` says so rather than reading 0.
    """
    from src.domains.knowledge.db_models import Course, Module, Topic
    from src.domains.personal_learning.db_models import FlashcardReview
    from src.domains.progress.db_models import StudySession

    moment = now or datetime.now(UTC)
    results: dict[str, GoalMeasurement] = {}

    manual = [goal for goal in goals if goal.metric_kind == "manual"]
    for goal in manual:
        results[goal.id] = GoalMeasurement(current_value=goal.current_value, measured=False)

    derived = [goal for goal in goals if goal.metric_kind != "manual"]
    if not derived:
        return results

    user_id = derived[0].user_id
    kinds = {goal.metric_kind for goal in derived}
    # Accumulating kinds count from each goal's own creation date, so the window to fetch is the
    # earliest of them; per-goal attribution happens below.
    earliest = min(
        (goal.created_at for goal in derived if goal.created_at is not None), default=moment
    )

    factory = get_session_factory()
    async with factory() as session:
        sessions: list[tuple[datetime, float | None, str | None, str | None]] = []
        if "focused_minutes" in kinds:
            sessions = list(
                (
                    await session.execute(
                        select(
                            StudySession.start_time,
                            StudySession.duration,
                            StudySession.course_id,
                            StudySession.topic_id,
                        ).where(
                            StudySession.user_id == user_id,
                            StudySession.start_time >= earliest,
                        )
                    )
                ).all()
            )

        reviews: list[tuple[datetime]] = []
        if "cards_reviewed" in kinds:
            reviews = list(
                (
                    await session.execute(
                        select(FlashcardReview.reviewed_at).where(
                            FlashcardReview.user_id == user_id,
                            FlashcardReview.reviewed_at >= earliest,
                        )
                    )
                ).all()
            )

        completions: list[tuple[datetime, str]] = []
        if "topics_mastered" in kinds:
            completions = list(
                (
                    await session.execute(
                        select(Topic.completed_at, Course.id)
                        .select_from(Topic)
                        .join(Module, Topic.module_id == Module.id)
                        .join(Course, Module.course_id == Course.id)
                        .where(
                            Course.user_id == user_id,
                            Course.archived.is_(False),
                            Topic.completed_at.is_not(None),
                            Topic.completed_at >= earliest,
                        )
                    )
                ).all()
            )

        course_progress: dict[str, float] = {}
        if "course_progress" in kinds:
            course_ids = [goal.course_id for goal in derived if goal.course_id]
            if course_ids:
                course_progress = {
                    row[0]: float(row[1] or 0.0)
                    for row in (
                        await session.execute(
                            select(Course.id, Course.progress).where(
                                Course.user_id == user_id, Course.id.in_(course_ids)
                            )
                        )
                    ).all()
                }

    readiness: dict[str, float | None] = {}
    if "prep_readiness" in kinds:
        prep_ids = [goal.prep_id for goal in derived if getattr(goal, "prep_id", None)]
        if prep_ids:
            try:
                from src.domains.personal_learning.services import prep_readiness as readiness_svc

                progress_by_prep = await readiness_svc.load_for_preparations(prep_ids)
                readiness = {
                    prep_id: progress.progress_percent
                    for prep_id, progress in progress_by_prep.items()
                }
            except Exception:
                # A readiness failure leaves those goals unmeasured rather than reading zero, and
                # must not take the rest of the list down with it.
                logger.warning("Goal readiness unavailable", extra={"user_id": user_id})

    for goal in derived:
        since = goal.created_at
        value: float | None = None

        if goal.metric_kind == "focused_minutes":
            value = sum(
                float(duration or 0.0)
                for start_time, duration, course_id, topic_id in sessions
                if (since is None or start_time >= since)
                and (goal.course_id is None or course_id == goal.course_id)
                and (goal.topic_id is None or topic_id == goal.topic_id)
            )
        elif goal.metric_kind == "cards_reviewed":
            value = float(
                sum(1 for (reviewed_at,) in reviews if since is None or reviewed_at >= since)
            )
        elif goal.metric_kind == "topics_mastered":
            value = float(
                sum(
                    1
                    for completed_at, course_id in completions
                    if (since is None or completed_at >= since)
                    and (goal.course_id is None or course_id == goal.course_id)
                )
            )
        elif goal.metric_kind == "course_progress":
            value = course_progress.get(goal.course_id) if goal.course_id else None
        elif goal.metric_kind == "prep_readiness":
            value = readiness.get(getattr(goal, "prep_id", None) or "")

        results[goal.id] = GoalMeasurement(current_value=value, measured=value is not None)

    return results


def derived_progress(goal: Any, measurement: GoalMeasurement | None = None) -> float:
    """How far this goal has come, as a percent, from what actually measures it.

    **The one definition.** `Goal.progress` is a stored column that nothing writes: `update_progress`
    exists and has no callers anywhere in `src`, so before this a measured goal's `currentValue`
    moved while its `progress` sat at its default. Everything shaped by progress — the ring, the pace
    figure, `statusLabel`, `projectedOutcome`, at-risk and overdue counts, the portfolio average and
    every nightly snapshot — read the column, so all of them reported a goal that never moved. The
    first four derived goals in the database measure `currentValue=4.0` against `progress=0.0`.

    The rules, and what each refuses to invent:

    - A `manual` goal's progress is the learner's own figure, returned untouched. Nothing measures it,
      and overwriting it would discard what they typed.
    - An unmeasured goal keeps its stored value. A `course_progress` goal with no `courseId` has
      nothing to read, and reporting `0` would claim no progress where the truth is no measurement.
    - Progress is measured **against the learner's stated target**, so a preparation aiming at 85
      percent readiness is finished at 85, not at 100. That is what makes `statusLabel` turn
      `COMPLETED` when the learner said they were done rather than when the scale runs out.
    - When a *state* kind has no stated target, the target is 100 — the maximum of the scale the value
      is already expressed in, not a guess about the learner. `course_progress` and `prep_readiness`
      are both percentages. An *accumulating* kind gets no such default: minutes and cards have no
      natural maximum, so without a target there is no fraction to compute and the stored value
      stands.
    - Clamped to 0-100, matching `GoalResponse.progress` (`ge=0.0, le=100.0`), and rounded to one
      decimal like `pace_percent`.
    """
    stored = float(getattr(goal, "progress", 0.0) or 0.0)
    kind = getattr(goal, "metric_kind", None) or "manual"

    if kind == "manual" or measurement is None or measurement.current_value is None:
        return stored

    target = getattr(goal, "target_value", None)
    if target is None and kind in _STATE_KINDS:
        target = 100.0
    if target is None or float(target) <= 0:
        return stored

    fraction = float(measurement.current_value) / float(target) * 100.0
    return round(min(max(fraction, 0.0), 100.0), 1)


async def count_achieved_milestones(goal_ids: list[str]) -> dict[str, tuple[int, int]]:
    """`{goalId: (achieved, total)}` for a set of goals, in one query."""
    if not goal_ids:
        return {}

    from src.domains.progress.db_models import GoalMilestone

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    GoalMilestone.goal_id,
                    func.count(GoalMilestone.id),
                    func.count(GoalMilestone.id).filter(GoalMilestone.achieved_at.is_not(None)),
                )
                .where(GoalMilestone.goal_id.in_(goal_ids))
                .group_by(GoalMilestone.goal_id)
            )
        ).all()

    return {goal_id: (int(achieved or 0), int(total or 0)) for goal_id, total, achieved in rows}


@dataclass(frozen=True)
class GoalScheduleHistory:
    """What `GoalScheduleChange` says about one goal's deadline having moved."""

    #: How many times the deadline was pushed **later**. Moving a deadline earlier is not an extension
    #: and is excluded, as is setting a first deadline on a goal that had none — neither buys the
    #: learner time, and counting them would inflate the one number that is supposed to mean "this
    #: goal has been given more room than it started with".
    extended_count: int
    #: How many of those extensions the **system** made, unprompted.
    #:
    #: A narrower count than `extended_count` on purpose, and the two must not be conflated. The wire
    #: publishes the wider one, because the learner's question is "has this deadline moved" and it does
    #: not matter who moved it. The ladder's extension budget reads this one, because the budget exists
    #: to stop the *system* extending indefinitely rather than having a conversation — a learner moving
    #: their own deadline is them stating a new intention, and spending their budget on it would mean
    #: refusing to help someone for the crime of having re-planned.
    system_extended_count: int
    #: The deadline this goal started with, from the earliest recorded change. `None` when nothing has
    #: moved, or when the first recorded change was a date being set for the first time.
    #:
    #: This is the denominator `elapsed_percent` should arguably be using. It is published rather than
    #: applied, because re-basing the window would change every pace figure on every surface and that
    #: is a behaviour change with its own decision to make.
    original_target_date: datetime | None


async def derive_schedule_history(goal_ids: list[str]) -> dict[str, GoalScheduleHistory]:
    """`{goalId: GoalScheduleHistory}` for a set of goals, in one query.

    Rows are fetched for the whole set and attributed in memory, the same shape
    `derive_current_values` uses for event rows and for the same reason: a page of twenty goals must
    not become twenty round trips to fill one column.

    In memory rather than in SQL because the interesting figure is *the previous date on the earliest
    row*, not the minimum previous date. A deadline pulled earlier and then pushed out again has a
    minimum that never was the goal's original window, and an aggregate cannot tell the difference. The
    row count is small — a goal accumulates a change only when its deadline actually moves.

    Goals with no recorded change are absent from the result rather than present with zeros, matching
    `count_achieved_milestones`. The caller defaults them, which keeps "never moved" and "moved zero
    times" from needing to be different things.
    """
    if not goal_ids:
        return {}

    from src.domains.progress.db_models import GoalScheduleChange

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    GoalScheduleChange.goal_id,
                    GoalScheduleChange.previous_date,
                    GoalScheduleChange.new_date,
                    GoalScheduleChange.reason,
                )
                .where(GoalScheduleChange.goal_id.in_(goal_ids))
                .order_by(GoalScheduleChange.created_at.asc())
            )
        ).all()

    counts: dict[str, int] = {}
    system_counts: dict[str, int] = {}
    originals: dict[str, datetime | None] = {}
    for goal_id, previous_date, new_date, reason in rows:
        if goal_id not in counts:
            counts[goal_id] = 0
            system_counts[goal_id] = 0
            # The earliest row's previous date, whether or not that row was an extension. A deadline
            # pulled forward and later pushed back still started somewhere.
            originals[goal_id] = previous_date
        if previous_date is None or new_date is None:
            continue
        if _utc(new_date) > _utc(previous_date):
            counts[goal_id] += 1
            if reason == "system_extended":
                system_counts[goal_id] += 1

    return {
        goal_id: GoalScheduleHistory(
            extended_count=count,
            system_extended_count=system_counts.get(goal_id, 0),
            original_target_date=originals.get(goal_id),
        )
        for goal_id, count in counts.items()
    }


async def get_goal_portfolio(*, user_id: str, now: datetime | None = None) -> GoalPortfolio:
    """Counts for the goals section, including how many are behind their own schedule.

    **Moved here from `reflect_aggregates`**, which re-exports it — see `GoalPortfolio`. One query
    over four columns, then pure arithmetic, so the whole portfolio costs the same as a page of it.
    This is what `/progress/goals/summary` reads: the goals list is paginated, so a page of twenty
    cannot produce a portfolio average, and asking the client to sum pages would give a different
    answer depending on how far the learner had scrolled.

    **Cancelled goals no longer count towards `averageProgress`.** They used to, which contradicted
    this function's own stated rule — archived goals were excluded because "averaging them in would
    make abandoning a goal look like a drop in performance", and a cancelled goal is abandoned by a
    more explicit route than an archived one. They were also counted in neither `active` nor
    `completed`, so a learner who cancelled a goal at 5% saw their average fall with no visible
    cause.
    """
    from src.domains.progress.db_models import Goal

    moment = now or datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as session:
        goals = list(
            (await session.execute(select(Goal).where(Goal.user_id == user_id))).scalars().all()
        )

    # Whole rows and a measurement pass, where this used to select four columns and average the
    # stored `progress`. It has to: that column is never written, so the average was the average of a
    # number that never moved, and a learner whose course went from 0 to 60 percent saw no change in
    # the figure their goals section leads with. Still bounded — `derive_current_values` issues one
    # query per metric kind present, not one per goal.
    measurements = await derive_current_values(goals, now=moment) if goals else {}

    active = completed = at_risk = due_soon = overdue = 0
    progress_values: list[float] = []

    for goal in goals:
        status, created_at, target_date = goal.status, goal.created_at, goal.target_date
        if status in ("ARCHIVED", "CANCELLED"):
            continue
        value = derived_progress(goal, measurements.get(goal.id))
        progress_values.append(value)

        if status == "COMPLETED":
            completed += 1
            continue
        if status != "ACTIVE":
            continue

        active += 1
        if is_at_risk(progress=value, created_at=created_at, target_date=target_date, now=moment):
            at_risk += 1
        if is_due_soon(status=status, progress=value, target_date=target_date, now=moment):
            due_soon += 1
        if is_overdue(status=status, progress=value, target_date=target_date, now=moment):
            overdue += 1

    return GoalPortfolio(
        active=active,
        completed=completed,
        at_risk=at_risk,
        due_soon=due_soon,
        overdue=overdue,
        average_progress=(
            round(sum(progress_values) / len(progress_values), 1) if progress_values else None
        ),
    )


@dataclass(frozen=True)
class GoalWeek:
    """One week of a goal's plan: what was scheduled, and what was recorded as done."""

    #: The Monday of the week, as a date.
    week_start: date
    planned: int
    completed: int


async def get_goal_momentum(
    *, user_id: str, goal_id: str, weeks: int, now: datetime | None = None
) -> list[GoalWeek]:
    """Planned versus completed sessions per week for one goal, oldest week first.

    Reads `ScheduleBlock`, which is what a *goal plan* is (Decision R keeps that distinct from a
    `StudyPlan`). `regenerate_goal_plan` writes one block per preferred weekday per week, so "planned"
    is a count of those blocks bucketed by the week they were scheduled for.

    **`completed` counts blocks with a `completedAt`, and reads zero until learners start marking them.**
    That column was added for this, because completion was recorded nowhere. It is deliberately not
    inferred from a `StudySession` overlapping the block's window: without a `scheduleBlockId` link that
    is a time coincidence, and it would credit a learner who sat down at the right hour and studied
    something else.

    Weeks with no planned blocks are **included, at zero**, because the question is "did the plan get
    done" and a week the learner scheduled nothing is part of that answer. This is the opposite of the
    activity feed's daily counts, where a missing day means nothing was recorded and is therefore
    omitted — here the absence is itself the measurement.

    Bucketed by ISO week starting Monday, in UTC. The blocks themselves are written at a fixed 09:00
    UTC by the planner, so a learner-local bucket would be precision this data does not have.
    """
    from src.domains.progress.db_models import ScheduleBlock

    first_monday = _first_monday(weeks=weeks, now=now)
    rows = await _plan_blocks(
        user_id=user_id,
        first_monday=first_monday,
        goal_scope=(ScheduleBlock.goal_id == goal_id),
    )

    return _bucket_weeks(rows, first_monday=first_monday, weeks=weeks)


def _first_monday(*, weeks: int, now: datetime | None) -> date:
    """The Monday that opens a backward window of `weeks`, including the current week."""
    moment = now or datetime.now(UTC)
    this_monday = moment.date() - timedelta(days=moment.weekday())
    return this_monday - timedelta(weeks=weeks - 1)


async def _plan_blocks(*, user_id: str, first_monday: date, goal_scope) -> list:
    """`(startAt, completedAt)` for the goal-plan blocks inside the window.

    `goal_scope` is the caller's predicate over `ScheduleBlock.goalId`: one goal for the goal chart,
    "attached to any goal" for the portfolio chart. Shared so the two charts cannot end up reading a
    different window or a different completion column, which is how a portfolio total stops matching
    the sum of its parts.
    """
    from src.domains.progress.db_models import ScheduleBlock

    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(ScheduleBlock.start_at, ScheduleBlock.completed_at).where(
                    ScheduleBlock.user_id == user_id,
                    goal_scope,
                    ScheduleBlock.start_at
                    >= datetime.combine(first_monday, datetime.min.time(), tzinfo=UTC),
                )
            )
        ).all()


def _bucket_weeks(rows, *, first_monday: date, weeks: int) -> list[GoalWeek]:
    """Bucket blocks into ISO weeks starting Monday, oldest first, empty weeks at zero."""
    buckets: dict[date, list[int]] = {
        first_monday + timedelta(weeks=offset): [0, 0] for offset in range(weeks)
    }

    for start_at, completed_at in rows:
        week_start = start_at.date() - timedelta(days=start_at.weekday())
        bucket = buckets.get(week_start)
        if bucket is None:
            # A block scheduled beyond the requested window. Counted nowhere rather than folded into
            # the nearest week, which would overstate that week's plan.
            continue
        bucket[0] += 1
        if completed_at is not None:
            bucket[1] += 1

    return [
        GoalWeek(week_start=week_start, planned=planned, completed=completed)
        for week_start, (planned, completed) in sorted(buckets.items())
    ]


async def get_portfolio_momentum(
    *, user_id: str, weeks: int, now: datetime | None = None
) -> list[GoalWeek]:
    """Planned versus completed sessions per week across **every** goal the learner holds.

    The `/reflect/goals` page draws one momentum chart above the whole list rather than one per goal, so
    this exists alongside `get_goal_momentum`. The alternative — a client summing per-goal responses —
    would mean one request per goal to draw a single chart.

    **Only blocks attached to a goal are counted.** `ScheduleBlock.goalId` is nullable, and a block with
    no goal is part of the learner's schedule but not part of any goal's plan. Counting those would make
    the portfolio chart taller than the sum of the goal charts beneath it, with nothing on the page to
    explain the difference.

    Shares `_plan_blocks` and `_bucket_weeks` with the per-goal read, so the two charts cannot describe
    different windows or count completion differently.
    """
    from src.domains.progress.db_models import ScheduleBlock

    first_monday = _first_monday(weeks=weeks, now=now)
    rows = await _plan_blocks(
        user_id=user_id,
        first_monday=first_monday,
        goal_scope=ScheduleBlock.goal_id.is_not(None),
    )
    return _bucket_weeks(rows, first_monday=first_monday, weeks=weeks)


async def portfolio_completion_ever_recorded(*, user_id: str) -> bool:
    """Whether this learner has **ever** marked any goal-plan block done.

    Asked of their whole history rather than of the window, for the reason
    `completion_ever_recorded` documents: a learner who worked their plan two months ago and then
    paused must not have their chart captioned "not tracked yet" (Decision Y).
    """
    from src.domains.progress.db_models import ScheduleBlock

    factory = get_session_factory()
    async with factory() as session:
        found = (
            await session.execute(
                select(ScheduleBlock.id)
                .where(
                    ScheduleBlock.user_id == user_id,
                    ScheduleBlock.goal_id.is_not(None),
                    ScheduleBlock.completed_at.is_not(None),
                )
                .limit(1)
            )
        ).first()
    return found is not None


async def completion_ever_recorded(*, user_id: str, goal_id: str) -> bool:
    """Whether any block for this goal has **ever** been marked done.

    Deliberately not "did anything complete inside the requested window", which is what counting the
    returned weeks would answer. A learner who worked through their plan two months ago and has done
    nothing for a fortnight would come back `False` from that reading, and the client would caption
    their chart "not tracked yet" — false, and dismissive of work they actually did.

    Its own query, and a cheap one: existence against the `(goalId, startAt)` index with a limit of one.
    """
    from src.domains.progress.db_models import ScheduleBlock

    factory = get_session_factory()
    async with factory() as session:
        found = (
            await session.execute(
                select(ScheduleBlock.id)
                .where(
                    ScheduleBlock.user_id == user_id,
                    ScheduleBlock.goal_id == goal_id,
                    ScheduleBlock.completed_at.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return found is not None


#: Average progress at or above which a portfolio is described as strong rather than steady.
STRONG_PORTFOLIO_PROGRESS = 75.0

#: What the goals page should lead with, as a token. The client owns the sentence.
GoalPortfolioHeadline = Literal[
    "none", "overdue", "at_risk", "due_soon", "all_complete", "strong", "steady"
]


def portfolio_headline(portfolio: GoalPortfolio) -> GoalPortfolioHeadline:
    """Which situation the goals page should open on, from figures it already publishes.

    **A token, not a sentence.** The fixture's hero read *"You have 4 active goals with an average
    progress of 58%. Two deadlines are approaching this week."* — two numbers baked into prose that
    could disagree with the tiles beneath it. Publishing the numbers and a token instead makes that
    impossible: the greeting is rendered from the same fields as the tiles, and the wording is the
    client's, which is right because a mobile hero and a web hero want different sentences.

    **The ladder is here rather than in the client** for the reason Decision O gives for action
    targets: choosing which fact is most urgent is a judgement about the learner's data, and two
    clients making it separately would eventually disagree about whether an overdue goal or a
    slipping one deserves the top of the page.

    Ordered by what the learner most needs to know:

    1. `none` — no goals at all. Not "steady at 0%", which would describe a portfolio that does not
       exist.
    2. `overdue` — a deadline has already passed. Ahead of `at_risk` because it is already true rather
       than projected.
    3. `at_risk` — behind its own schedule by more than `AT_RISK_LAG_POINTS`.
    4. `due_soon` — a deadline inside `DUE_SOON_DAYS`, still ahead.
    5. `all_complete` — everything finished and nothing active. A real state, and the only one where
       the next move is to set a goal rather than to work on one.
    6. `strong` / `steady` — nothing pressing; the two are split on `averageProgress` so the page is
       not congratulatory about a portfolio sitting at 12%.

    `averageProgress` is `None` for a learner with no goals, which case 1 has already taken, so the
    comparison below cannot be reached with a null.
    """
    if not portfolio.active and not portfolio.completed:
        return "none"
    if portfolio.overdue:
        return "overdue"
    if portfolio.at_risk:
        return "at_risk"
    if portfolio.due_soon:
        return "due_soon"
    if not portfolio.active:
        return "all_complete"
    average = portfolio.average_progress or 0.0
    return "strong" if average >= STRONG_PORTFOLIO_PROGRESS else "steady"
