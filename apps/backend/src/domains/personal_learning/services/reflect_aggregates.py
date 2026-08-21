"""Reflect-surface aggregates: mastery by subject, and the goal portfolio.

Separate from `reflection_metrics` because the questions are different. A reflection measures
*a period*; these describe *the present*. The dashboard shows both side by side, so keeping
them in one module would blur which figures move when the range toggle changes and which do
not.

Composed by the dashboard read model rather than read directly by a route, so that every
figure the surface shows has exactly one definition. Two of the numbers the design displays
are deltas and are deliberately `None` here — see `SubjectMastery.change` and
`GoalPortfolio.average_progress`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from src.domains.knowledge.db_models import Course, Module, Topic
from src.domains.progress.db_models import Goal
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

#: How far a goal may fall behind its own elapsed time before it is called at risk, in
#: percentage points. One threshold, defined once, used by every surface that labels a goal —
#: the alternative is `/goals` and `/reflect/goals` disagreeing about the same goal.
AT_RISK_LAG_POINTS = 15.0


@dataclass(frozen=True)
class SubjectMastery:
    """One row of "Mastery by subject". A subject is a course (Decision H).

    A course is already the thing a learner recognises as a subject, and it already stores
    both the label and the completion figure. The alternative — a subject as a tag spanning
    courses — would need a new grouping entity and would leave `/activity/subjects/:id`
    pointing at nothing real.
    """

    course_id: str
    title: str
    category: str | None
    mastery_percent: float
    topics_total: int
    topics_completed: int
    #: Percentage points gained over the requested window. `None` until the daily snapshot
    #: exists: `Course.progress` is mutable in place, so yesterday's value is not recoverable
    #: and a delta cannot be invented from a single current number.
    change: float | None = None


@dataclass(frozen=True)
class GoalPortfolio:
    """The counts above the goals list."""

    active: int
    completed: int
    at_risk: int
    #: Mean progress across active and completed goals, or `None` when there are none.
    #: Archived goals are excluded: they are goals the learner stopped, and averaging them in
    #: would make abandoning a goal look like a drop in performance.
    average_progress: float | None


async def list_subject_mastery(*, user_id: str, limit: int | None = None) -> list[SubjectMastery]:
    """The learner's unarchived courses, strongest first.

    `mastery_percent` is recomputed here from the topics rather than read from
    `Course.progress`. The stored column is maintained by `recount_course_progress` and should
    agree — but this query already needs the topic counts for `topicsTotal` and
    `topicsCompleted`, which the design shows beside the bar, so deriving the percentage from
    the same rows costs nothing and removes the chance of the bar disagreeing with the
    caption beneath it.
    """
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Course.id,
                    Course.title,
                    Course.category,
                    func.count(Topic.id),
                    func.count(Topic.id).filter(Topic.completed.is_(True)),
                )
                .select_from(Course)
                .outerjoin(Module, Module.course_id == Course.id)
                .outerjoin(Topic, Topic.module_id == Module.id)
                .where(Course.user_id == user_id, Course.archived.is_(False))
                .group_by(Course.id, Course.title, Course.category)
            )
        ).all()

    subjects = [
        SubjectMastery(
            course_id=course_id,
            title=title,
            category=category,
            # Matches `recount_course_progress` to one decimal place, so the two cannot
            # disagree in the last digit.
            mastery_percent=round(completed / total * 100, 1) if total else 0.0,
            topics_total=int(total or 0),
            topics_completed=int(completed or 0),
        )
        for course_id, title, category, total, completed in rows
    ]

    # Strongest first, then by title so the order is stable between requests. Without the
    # tiebreak two courses at the same percentage swap places on refresh.
    subjects.sort(key=lambda s: (-s.mastery_percent, s.title.lower()))
    return subjects[:limit] if limit else subjects


@dataclass(frozen=True)
class MasteryOnDay:
    """Mastery as it stood at one instant: per course, and overall across them."""

    #: `{courseId: masteryPercent}`, exactly what the snapshot's `subjectMastery` stores.
    by_course: dict[str, float]
    #: Completed topics over all topics in the learner's own unarchived courses. `None` when
    #: they have no topics, because a learner with nothing to master has no mastery percentage
    #: — as against having zero.
    overall_percent: float | None
    topics_total: int
    topics_completed: int


async def subject_mastery_on(*, user_id: str, as_of: datetime) -> MasteryOnDay:
    """Mastery as of an instant, for the daily snapshot.

    The historical counterpart to `list_subject_mastery`, and it lives here so that "how
    complete is this course" has one definition and one rounding rule regardless of which day
    is being asked about.

    **It counts `Topic.completedAt <= as_of` where the present-tense query counts
    `Topic.completed`, and that difference is the approximation Decision P documents.** Two
    ways it can understate a past day, both worth knowing before reading a curve:

    - The denominator is the course's topic count *now*. Topics added since make earlier
      progress look smaller than it felt at the time.
    - A topic completed and later reopened has had its `completedAt` cleared, so it reads as
      never completed — including for days when it genuinely was.

    Both distortions push the same way, which is the redeeming part: a reconstructed trend
    understates the past and therefore never invents growth that did not happen. Rows written
    from this for a past day are flagged `reconstructed` so the client can say so.

    For *today* the same query is exact, since "now" is the denominator the learner is
    actually working against — which is why one function serves the nightly writer and the
    backfill instead of two that could drift.
    """
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Course.id,
                    func.count(Topic.id),
                    func.count(Topic.id).filter(
                        Topic.completed_at.is_not(None), Topic.completed_at <= as_of
                    ),
                )
                .select_from(Course)
                .outerjoin(Module, Module.course_id == Course.id)
                .outerjoin(Topic, Topic.module_id == Module.id)
                .where(Course.user_id == user_id, Course.archived.is_(False))
                .group_by(Course.id)
            )
        ).all()

    by_course: dict[str, float] = {}
    topics_total = 0
    topics_completed = 0

    for course_id, total, completed in rows:
        total_count = int(total or 0)
        completed_count = int(completed or 0)
        topics_total += total_count
        topics_completed += completed_count
        # A course with no topics is recorded as 0.0 rather than omitted: the key tells the
        # trend the course existed on that day, which is what makes a later delta meaningful.
        by_course[course_id] = round(completed_count / total_count * 100, 1) if total_count else 0.0

    return MasteryOnDay(
        by_course=by_course,
        overall_percent=(round(topics_completed / topics_total * 100, 1) if topics_total else None),
        topics_total=topics_total,
        topics_completed=topics_completed,
    )


async def get_goal_portfolio(*, user_id: str, now: datetime | None = None) -> GoalPortfolio:
    """Counts for the goals section, including how many are behind their own schedule."""
    moment = now or datetime.now(UTC)

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Goal.status, Goal.progress, Goal.created_at, Goal.target_date).where(
                    Goal.user_id == user_id
                )
            )
        ).all()

    active = completed = at_risk = 0
    progress_values: list[float] = []

    for status, progress, created_at, target_date in rows:
        if status == "ARCHIVED":
            continue
        value = float(progress or 0.0)
        progress_values.append(value)

        if status == "COMPLETED":
            completed += 1
            continue
        if status != "ACTIVE":
            continue

        active += 1
        if is_at_risk(progress=value, created_at=created_at, target_date=target_date, now=moment):
            at_risk += 1

    return GoalPortfolio(
        active=active,
        completed=completed,
        at_risk=at_risk,
        average_progress=(
            round(sum(progress_values) / len(progress_values), 1) if progress_values else None
        ),
    )


def is_at_risk(
    *,
    progress: float,
    created_at: datetime | None,
    target_date: datetime | None,
    now: datetime,
) -> bool:
    """Whether a goal has fallen behind the pace its own deadline implies.

    Pure, because this is the arithmetic that decides a label the learner sees and it should
    be testable without a database.

    A goal with no `targetDate` is **never** at risk. There is no pace to fall behind when
    there is no deadline, and calling an open-ended goal "needs attention" for making slow
    progress would be inventing a commitment the learner never made.

    A goal already past its deadline and unfinished is at risk regardless of progress.
    """
    if target_date is None or created_at is None:
        return False
    if progress >= 100:
        return False
    if now >= target_date:
        return True

    total = (target_date - created_at).total_seconds()
    if total <= 0:
        return False

    elapsed_percent = (now - created_at).total_seconds() / total * 100
    return (elapsed_percent - progress) > AT_RISK_LAG_POINTS
