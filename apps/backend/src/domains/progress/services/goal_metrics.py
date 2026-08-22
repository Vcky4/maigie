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
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select

from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

#: How far a goal may fall behind its own elapsed time before it is called at risk, in percentage
#: points. One threshold, defined once, read by every surface that labels a goal — the alternative
#: is `/goals` and `/reflect/goals` disagreeing about the same goal in front of the same learner.
AT_RISK_LAG_POINTS = 15.0

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


# ---------------------------------------------------------------------------
# Pace and status — pure
# ---------------------------------------------------------------------------


def elapsed_percent(
    *, created_at: datetime | None, target_date: datetime | None, now: datetime
) -> float | None:
    """How far through its own window a goal is, 0-100, or `None` when it has no window.

    `None` rather than `0` for an open-ended goal. A goal with no deadline is not at the start of
    anything, and every consumer here has to treat "no schedule" differently from "no progress".
    """
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
