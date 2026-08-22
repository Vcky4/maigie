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
from datetime import UTC, date, datetime

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

    #: `{courseId: masteryPercent}`, exactly what the snapshot's `subjectMastery` stores. `None`
    #: when the day is not measurable at all — see `undated_completions`.
    by_course: dict[str, float] | None
    #: Completed topics over all topics in the learner's own unarchived courses. `None` when
    #: they have no topics, because a learner with nothing to master has no mastery percentage
    #: — as against having zero — and `None` when the day is not measurable.
    overall_percent: float | None
    topics_total: int
    topics_completed: int
    #: Topics marked complete with **no `completedAt`**, so their completion cannot be placed in
    #: time. Reported rather than swallowed because it is the difference between a mastery figure
    #: that is exact and one that is unknowable, and the caller decides which it can live with.
    undated_completions: int = 0


async def subject_mastery_on(
    *, user_id: str, as_of: datetime, undated_are_complete: bool
) -> MasteryOnDay:
    """Mastery as of an instant, for the daily snapshot.

    The historical counterpart to `list_subject_mastery`, and it lives here so that "how complete
    is this course" has one definition and one rounding rule regardless of which day is asked
    about.

    It counts `Topic.completedAt <= as_of` where the present-tense query counts `Topic.completed`,
    and the gap between those two predicates turned out to be much larger than Decision P assumed.
    **Half the completed topics in the database have no `completedAt` at all** — 27 of 53 when this
    was measured, and for some learners every single one. Those completions are real; they simply
    cannot be placed in time, because nothing recorded when they happened. Decision P allowed for
    the reopened-topic case and judged understating acceptable; it did not allow for a learner
    whose mastery reads a flat `0.0` across four hundred topics, which is not an understatement but
    a fabrication in the other direction.

    So `undated_are_complete` makes the caller state which question it is asking, and there is no
    safe default:

    - **`True`** — "what is complete now". Correct for the nightly writer, which records the day
      that just ended: an undated completion certainly happened before a few hours ago, and the
      figure then agrees with the dashboard's `list_subject_mastery` instead of contradicting it.
    - **`False`** — "what was complete on this specific past day". Correct for the backfill. When
      any completion is undated the answer is genuinely **unknown**, so `by_course` and
      `overall_percent` come back `None` rather than as a number that excludes work the learner
      did. A null renders as no point on the chart; `0.0` renders as a floor they never stood on.

    One distortion remains in both modes and is unavoidable: the denominator is the course's topic
    count *now*, so topics added since make earlier progress look smaller than it felt. That one
    only ever understates, so a reconstructed trend still never invents growth.
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
                    func.count(Topic.id).filter(
                        Topic.completed.is_(True), Topic.completed_at.is_(None)
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
    undated_total = 0

    for course_id, total, dated_completed, undated in rows:
        total_count = int(total or 0)
        undated_count = int(undated or 0)
        completed_count = int(dated_completed or 0) + (undated_count if undated_are_complete else 0)

        topics_total += total_count
        topics_completed += completed_count
        undated_total += undated_count
        # A course with no topics is recorded as 0.0 rather than omitted: the key tells the trend
        # the course existed on that day, which is what makes a later delta meaningful.
        by_course[course_id] = round(completed_count / total_count * 100, 1) if total_count else 0.0

    # Asking about a specific past day while holding completions that cannot be dated: the honest
    # answer is that this day's mastery was not measured, not that it was zero.
    unmeasurable = not undated_are_complete and undated_total > 0

    return MasteryOnDay(
        by_course=None if unmeasurable else by_course,
        overall_percent=(
            None
            if unmeasurable or not topics_total
            else round(topics_completed / topics_total * 100, 1)
        ),
        topics_total=topics_total,
        topics_completed=topics_completed,
        undated_completions=undated_total,
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


async def subject_mastery_series(
    *, user_id: str, days: list[date], undated_are_complete: bool
) -> dict[date, MasteryOnDay]:
    """Mastery for many days in two queries instead of one per day.

    `subject_mastery_on` is right for a single day and wrong to call ninety times: it re-reads the
    same topic rows for every date. This reads each course's topic count once and the completion
    dates once, then walks the days in order accumulating completions as it passes them.

    The result is identical to calling `subject_mastery_on` per day — same predicate, same rounding,
    same null rule — which matters because the nightly writer uses the single-day function and the
    backfill uses this one, and a chart would show a step where two definitions met.
    """
    factory = get_session_factory()
    async with factory() as session:
        totals = (
            await session.execute(
                select(
                    Course.id,
                    func.count(Topic.id),
                    func.count(Topic.id).filter(
                        Topic.completed.is_(True), Topic.completed_at.is_(None)
                    ),
                )
                .select_from(Course)
                .outerjoin(Module, Module.course_id == Course.id)
                .outerjoin(Topic, Topic.module_id == Module.id)
                .where(Course.user_id == user_id, Course.archived.is_(False))
                .group_by(Course.id)
            )
        ).all()

        completions = (
            await session.execute(
                select(Course.id, Topic.completed_at)
                .select_from(Topic)
                .join(Module, Topic.module_id == Module.id)
                .join(Course, Module.course_id == Course.id)
                .where(
                    Course.user_id == user_id,
                    Course.archived.is_(False),
                    Topic.completed_at.is_not(None),
                )
                .order_by(Topic.completed_at.asc())
            )
        ).all()

    course_totals = {course_id: int(total or 0) for course_id, total, _ in totals}
    course_undated = {course_id: int(undated or 0) for course_id, _, undated in totals}
    undated_total = sum(course_undated.values())

    # Walked in date order with a cursor over the completions, so each is counted once across the
    # whole series rather than re-scanned per day.
    ordered = sorted(completions, key=lambda row: row[1])
    dated_by_course: dict[str, int] = {course_id: 0 for course_id in course_totals}
    cursor = 0

    series: dict[date, MasteryOnDay] = {}
    for day in sorted(days):
        # `as_of` is the end of the learner's day, which the caller has already resolved into the
        # date key; completions are compared against the day boundary the same way.
        while cursor < len(ordered) and ordered[cursor][1].date() <= day:
            course_id = ordered[cursor][0]
            if course_id in dated_by_course:
                dated_by_course[course_id] += 1
            cursor += 1

        by_course: dict[str, float] = {}
        topics_total = 0
        topics_completed = 0
        for course_id, total in course_totals.items():
            completed = dated_by_course[course_id] + (
                course_undated[course_id] if undated_are_complete else 0
            )
            topics_total += total
            topics_completed += completed
            by_course[course_id] = round(completed / total * 100, 1) if total else 0.0

        unmeasurable = not undated_are_complete and undated_total > 0
        series[day] = MasteryOnDay(
            by_course=None if unmeasurable else by_course,
            overall_percent=(
                None
                if unmeasurable or not topics_total
                else round(topics_completed / topics_total * 100, 1)
            ),
            topics_total=topics_total,
            topics_completed=topics_completed,
            undated_completions=undated_total,
        )

    return series
