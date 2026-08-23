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
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select

from src.domains.knowledge.db_models import Course, Module, Topic
from src.domains.progress.db_models import Goal
from src.domains.progress.services import goal_metrics
from src.shared.database import get_session_factory
from src.shared.time import ensure_utc

from . import prep_readiness

logger = logging.getLogger(__name__)

#: Re-exported, **not** redefined. The threshold and the predicate live in
#: `progress.services.goal_metrics` because `Goal` is a `progress` entity, and Decision N asks for
#: one at-risk rule shared by `/progress/goals` and `/reflect/goals`. Importing here keeps the name
#: available to existing callers while there is only ever one number and one implementation.
AT_RISK_LAG_POINTS = goal_metrics.AT_RISK_LAG_POINTS
is_at_risk = goal_metrics.is_at_risk


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


#: Re-exported, **not** redefined — same arrangement as `is_at_risk` above and for the same reason.
#: `Goal` is a `progress` entity, so the portfolio arithmetic moved to `goal_metrics` when
#: `/progress/goals/summary` needed it. `progress` importing this module instead would have closed a
#: cycle, since this module imports `goal_metrics`.
GoalPortfolio = goal_metrics.GoalPortfolio


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


#: Re-exported, not reimplemented — see `GoalPortfolio` above.
get_goal_portfolio = goal_metrics.get_goal_portfolio


# `is_at_risk` and the goal portfolio were both defined here and are now imported at the top of this
# module. They moved rather than being copied: two implementations of the label or the count a
# learner reads on the same goal, one per surface, is the exact failure Decision N's "one threshold"
# clause exists to prevent.


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


@dataclass(frozen=True)
class SubjectActivity:
    """What a learner actually did on one course across a window.

    Separate from `SubjectMastery`, which answers "how far through the course are they". This answers
    "did they show up to it", and the two come from unrelated tables.

    **Every field here is `None` when nothing recorded it, never `0`** (Decision I) — with one converse
    that matters as much as the rule: once there is *any* evidence the learner tracked study time, a
    zero for a particular course is a genuine finding rather than a gap. `reflection_metrics.compute`
    settled that distinction with its `had_activity` gate and this follows it.
    """

    course_id: str
    #: Study sittings recorded against this course.
    sessions: int | None
    #: Minutes, from `StudySession.duration` — which is minutes, unlike `QuizSession.durationSeconds`.
    focused_minutes: float | None
    #: Distinct **learner-local** calendar days with a session on this course.
    active_days: int | None
    #: Knowledge checks first answered during the window, on topics of this course.
    knowledge_checks_answered: int
    #: Share of those first attempts that were correct. `None` when there were none — a percentage over
    #: an empty denominator is not `0`, it is unmeasured, the same rule
    #: `prepare_dashboard_service.summary` already follows for quiz accuracy.
    #:
    #: **Named for what it measures, and it is not "recall".** Quiz accuracy cannot be attributed to a
    #: course at all: `QuizSession.prepId` points at `ExamPrep` and its `topicId` at `PrepTopic`, and
    #: there is no join from either to `Course`. `TopicCheckAttempt` is the only correctness signal that
    #: reaches a course, through `Topic → Module.courseId`.
    knowledge_check_accuracy_percent: float | None


@dataclass(frozen=True)
class SubjectActivityMap:
    """Per-course activity, plus the one fact a caller needs to fill in the courses that have no row.

    `by_course` only holds courses with something recorded against them. Whether a course *without* a
    row should read `0` or `null` depends on `tracked_any_session`, which is a fact about the learner
    rather than about the course — so it travels with the map instead of each caller re-deriving it.
    Without it, a learner who tracks time and simply did not open Linear Algebra would see a dash where
    `0 sessions` is the truth.
    """

    by_course: dict[str, SubjectActivity]
    #: Whether the learner has **any** study session in the window. The gate between "did not work on
    #: this subject" (a finding) and "nothing was measured" (a gap).
    tracked_any_session: bool

    def for_course(self, course_id: str) -> SubjectActivity:
        """This course's activity, or the correctly-nulled empty row."""
        found = self.by_course.get(course_id)
        if found is not None:
            return found
        return empty_subject_activity(course_id, tracked_any_session=self.tracked_any_session)


async def list_subject_activity(
    *,
    user_id: str,
    since: date,
    until: date,
    timezone_,
) -> SubjectActivityMap:
    """Per-course activity across a window. Two queries, whatever the course count.

    **Study sessions are bucketed in Python, not in SQL**, because `activeDays` is a question about the
    learner's own calendar and the rest of this surface buckets days with `to_learner_local`. Pushing a
    `date()` into Postgres would bucket in UTC and disagree with `DailyLearningSnapshot.snapshotDate` at
    every day boundary. The volume is small — `StudySession` is written by one endpoint most learners
    never touch, which is the same reason Phase 2 gave for `focusedMinutes` being null so often.

    **Knowledge-check accuracy counts each topic's *first* attempt only.** `TopicCheckAttempt`'s own
    docstring records that the answer key ships to the browser so the verdict can be revealed without a
    round trip — which means later attempts on the same topic are near-always correct. Counting them
    would produce an accuracy that climbs towards 100% as a function of re-reading, not of knowing.
    """
    from src.domains.knowledge.db_models import TopicCheckAttempt
    from src.domains.progress.db_models import StudySession
    from src.shared.time import to_learner_local

    # Inclusive of both end days, in the learner's calendar. Widened by a day on each side in UTC
    # before filtering precisely in Python, so a session at 23:30 local on the last day is not dropped
    # by a UTC comparison.
    window_start = datetime.combine(since, datetime.min.time(), tzinfo=UTC) - timedelta(days=1)
    window_end = datetime.combine(until, datetime.max.time(), tzinfo=UTC) + timedelta(days=1)

    factory = get_session_factory()
    async with factory() as session:
        session_rows = (
            await session.execute(
                select(
                    StudySession.course_id,
                    StudySession.start_time,
                    StudySession.duration,
                ).where(
                    StudySession.user_id == user_id,
                    StudySession.start_time >= window_start,
                    StudySession.start_time <= window_end,
                )
            )
        ).all()

        # Each topic's first attempt, then only those that landed inside the window.
        first_attempts = (
            select(
                TopicCheckAttempt.topic_id.label("topic_id"),
                func.min(TopicCheckAttempt.created_at).label("first_at"),
            )
            .where(TopicCheckAttempt.user_id == user_id)
            .group_by(TopicCheckAttempt.topic_id)
            .subquery()
        )

        check_rows = (
            await session.execute(
                select(
                    Module.course_id,
                    func.count(TopicCheckAttempt.id),
                    func.count(TopicCheckAttempt.id).filter(TopicCheckAttempt.correct.is_(True)),
                )
                .select_from(TopicCheckAttempt)
                .join(
                    first_attempts,
                    (first_attempts.c.topic_id == TopicCheckAttempt.topic_id)
                    & (first_attempts.c.first_at == TopicCheckAttempt.created_at),
                )
                .join(Topic, Topic.id == TopicCheckAttempt.topic_id)
                .join(Module, Module.id == Topic.module_id)
                .where(
                    TopicCheckAttempt.user_id == user_id,
                    TopicCheckAttempt.created_at >= window_start,
                    TopicCheckAttempt.created_at <= window_end,
                )
                .group_by(Module.course_id)
            )
        ).all()

    # Whether the learner tracks study time *at all* in this window. This is the gate that decides
    # between "no sessions on this course" (a finding) and "nothing was measured" (a gap).
    tracked_any_session = bool(session_rows)

    per_course: dict[str, dict] = {}
    for course_id, start_time, duration in session_rows:
        if course_id is None:
            # A session not attributed to any course. Counted towards `tracked_any_session` above,
            # because it is evidence the learner records time, but attributable to no subject.
            continue
        local_day = to_learner_local(start_time, timezone_).date()
        if not (since <= local_day <= until):
            continue
        bucket = per_course.setdefault(course_id, {"sessions": 0, "minutes": 0.0, "days": set()})
        bucket["sessions"] += 1
        bucket["minutes"] += float(duration or 0.0)
        bucket["days"].add(local_day)

    checks = {
        course_id: (int(total or 0), int(correct or 0))
        for course_id, total, correct in check_rows
        if course_id is not None
    }

    course_ids = set(per_course) | set(checks)
    result: dict[str, SubjectActivity] = {}

    for course_id in course_ids:
        bucket = per_course.get(course_id)
        answered, correct = checks.get(course_id, (0, 0))

        result[course_id] = SubjectActivity(
            course_id=course_id,
            sessions=(bucket["sessions"] if bucket else (0 if tracked_any_session else None)),
            focused_minutes=(
                round(bucket["minutes"], 1) if bucket else (0.0 if tracked_any_session else None)
            ),
            active_days=(len(bucket["days"]) if bucket else (0 if tracked_any_session else None)),
            knowledge_checks_answered=answered,
            knowledge_check_accuracy_percent=(
                round(correct / answered * 100, 1) if answered else None
            ),
        )

    return SubjectActivityMap(by_course=result, tracked_any_session=tracked_any_session)


def empty_subject_activity(course_id: str, *, tracked_any_session: bool) -> SubjectActivity:
    """The row for a course with nothing recorded against it.

    Its own function so the null-versus-zero rule is written once. `tracked_any_session` is the caller's
    answer to "does this learner record study time at all", which is what makes `0` honest rather than a
    claim about a course nobody measured.
    """
    zero_or_none = 0 if tracked_any_session else None
    return SubjectActivity(
        course_id=course_id,
        sessions=zero_or_none,
        focused_minutes=(0.0 if tracked_any_session else None),
        active_days=zero_or_none,
        knowledge_checks_answered=0,
        knowledge_check_accuracy_percent=None,
    )


# ---------------------------------------------------------------------------
# Per-topic concept mastery
# ---------------------------------------------------------------------------

#: Re-exported, **not** redefined. The three-band ladder already exists in `prep_readiness`, keyed to
#: the `MASTERED` label `quiz_engine._update_topic_mastery` writes and the `WEAK_AREAS` filter
#: `quiz_engine.start_quiz` applies. A second set of thresholds would mean a topic called "strong" on
#: one screen and "needs attention" on another, which is the failure Decision N's one-threshold clause
#: exists to prevent — here applied to topics rather than goals.
MASTERY_STRONG_THRESHOLD = prep_readiness.MASTERY_STRONG_THRESHOLD
MASTERY_FOCUS_THRESHOLD = prep_readiness.MASTERY_FOCUS_THRESHOLD
mastery_band = prep_readiness.mastery_band

#: What a concept row can say about itself.
#:
#: `not_started` is the addition, and it is not cosmetic. `mastery_band(0.0)` returns `focus`, which the
#: design renders as "Needs attention" — a reasonable label for a topic the learner has worked and not
#: grasped, and the wrong label entirely for one they have never opened. Without this state, every
#: untouched topic in a new course would be flagged as a problem on first render.
ConceptStatus = Literal["not_started", "needs_attention", "growing", "strong"]

#: Where a concept's percentage came from. Published rather than inferred, because the two are not
#: equally informative and a client should be able to say so.
#:
#: `sections` is a genuine gradation — a topic part-way through its sections reads 40%. `completion` is
#: binary: `Topic` has no mastery column at all, so a topic with no sections can only be 0 or 100, and
#: the middle band is unreachable for it. In this database that is **94% of topics** (66 of 1056 have
#: any sections), so a client that treats the figure as a smooth percentage would be reading far more
#: precision into it than exists.
ConceptMasterySource = Literal["sections", "completion"]


def concept_status(*, mastery_percent: float | None, touched: bool) -> ConceptStatus:
    """Classify one concept, on the same ladder every other surface uses.

    `touched` is what separates "worked on and weak" from "never opened", which the percentage alone
    cannot express: both are `0`. Pure, so the labelling is testable without a database.
    """
    if not touched:
        return "not_started"
    band = mastery_band(mastery_percent)
    if band == "strong":
        return "strong"
    if band == "review":
        return "growing"
    return "needs_attention"


@dataclass(frozen=True)
class ConceptMastery:
    """One topic of one course, and how far through it the learner is.

    **`Topic` stores no mastery score.** `PrepTopic` has `masteryScore` and `targetMastery`; the
    knowledge-domain `Topic` this describes has only `completed` and `completedAt`. So every figure here
    is derived, and `source` names from what — see `ConceptMasterySource`.
    """

    topic_id: str
    title: str
    #: 0-100. `None` only when the topic has no sections *and* no completion flag to read, which the
    #: schema does not currently allow — kept nullable so a future topic kind without either does not
    #: have to be published as `0`.
    mastery_percent: float | None
    source: ConceptMasterySource
    sections_total: int
    sections_completed: int
    completed: bool
    status: ConceptStatus


async def list_concept_mastery(*, user_id: str, course_id: str) -> list[ConceptMastery]:
    """Every topic of one course, in the course's own order. One query.

    Scoped through `Module → Course.userId`, so another learner's course id yields an empty list rather
    than their topics.

    **No `UserTopicProgress` branch, and its absence is deliberate.** That table holds per-learner
    progress on *shared* courses, and this read is only ever reached for a course the learner owns —
    `list_subject_mastery` filters on `Course.userId`, and a subject is a course (Decision H). Writing
    the shared branch would be code no request can reach, which is worse than not writing it because it
    would look maintained.

    **No knowledge-check verdict either.** `TopicCheckAttempt` is one question per topic, re-answerable
    once the answer has been revealed, so a per-topic `correct` would be a coin-flip dressed as
    mastery. Its aggregate *is* published, at subject level, as
    `SubjectActivitySummary.knowledgeCheckAccuracyPercent` — where averaging over many topics makes it
    mean something.
    """
    from src.domains.knowledge.db_models import TopicSection

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Topic.id,
                    Topic.title,
                    Topic.completed,
                    func.count(TopicSection.id),
                    func.count(TopicSection.id).filter(TopicSection.completed.is_(True)),
                )
                .select_from(Topic)
                .join(Module, Module.id == Topic.module_id)
                .join(Course, Course.id == Module.course_id)
                .outerjoin(TopicSection, TopicSection.topic_id == Topic.id)
                .where(Course.id == course_id, Course.user_id == user_id)
                .group_by(Topic.id, Topic.title, Topic.completed, Module.order, Topic.order)
                .order_by(Module.order.asc(), Topic.order.asc())
            )
        ).all()

    concepts: list[ConceptMastery] = []
    for topic_id, title, completed, sections_total, sections_done in rows:
        total = int(sections_total or 0)
        done = int(sections_done or 0)

        if total > 0:
            # A real gradation: part-way through the sections is part-way through the topic.
            percent = round(done / total * 100, 1)
            source: ConceptMasterySource = "sections"
            # Opening one section is evidence of work even before any is finished. A topic marked
            # complete counts too, for the 100% case where sections were never ticked individually.
            touched = done > 0 or bool(completed)
        else:
            # Binary, because there is nothing else on the row to read.
            percent = 100.0 if completed else 0.0
            source = "completion"
            touched = bool(completed)

        concepts.append(
            ConceptMastery(
                topic_id=topic_id,
                title=title,
                mastery_percent=percent,
                source=source,
                sections_total=total,
                sections_completed=done,
                completed=bool(completed),
                status=concept_status(mastery_percent=percent, touched=touched),
            )
        )

    return concepts


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

#: What kind of thing happened. A closed set, because each one is a different query against a
#: different table and a client renders each with its own icon and phrasing.
EvidenceKind = Literal[
    "topic_completed",
    "section_completed",
    "study_session",
    "knowledge_check",
    "quiz_session",
    "practice_answer",
]


@dataclass(frozen=True)
class EvidenceItem:
    """One dated thing the learner actually did, attributable to a course.

    **Not built from `ActivityFeedEntry`, and that is the whole design.** The feed looks like the right
    source and cannot be: it has no `courseId` column at all — `entityType`/`entityId` are keys inside a
    nullable `context` JSON — and no writer has ever tagged an entry with `course` or `topic`, even
    though both are in the `ActivityEntity` literal. A feed filter would have looked correct in review
    and returned zero rows for every learner. These come from the tables that actually record the link.

    `value` and `unit` stay numeric rather than being formatted into `result` prose. The fixture carried
    strings like `"34 min"`; a number the client formats cannot disagree with the figure beside it, and
    a formatted string cannot be summed or compared.
    """

    id: str
    kind: EvidenceKind
    title: str
    #: The context that makes the title meaningful — the module a topic sits in, the topic a section
    #: belongs to. `None` when there is nothing to add.
    detail: str | None
    occurred_at: datetime
    value: float | None = None
    unit: str | None = None
    #: `True` / `False` for a knowledge check, `None` for everything else — a study session is not
    #: correct or incorrect.
    correct: bool | None = None

    def __post_init__(self) -> None:
        """Normalise `occurred_at` to an aware UTC instant.

        **This is load-bearing, and it is here rather than at each call site for a reason.** These items
        are merged from four tables and then sorted as one list, and the tables disagree about whether
        their timestamp column carries an offset: `Topic.completedAt`, `TopicSection.completedAt` and
        `TopicCheckAttempt.createdAt` are `timestamptz` while `StudySession.startTime` is not. `sort`
        cannot order a naive instant against an aware one, so subject detail and subject insight returned
        **500 for any course that had both a dated topic completion and a study session** — which is why
        it passed review and passed the courses this was first checked against.

        Doing it in `__post_init__` means a fifth evidence kind cannot reintroduce the bug by forgetting.
        """
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))


async def list_course_evidence(
    *, user_id: str, course_id: str, limit: int = 12
) -> list[EvidenceItem]:
    """Recent dated evidence for one course, newest first.

    Four small queries — completed topics, completed sections, study sessions, first knowledge-check
    attempts — merged and sorted in Python. Each is capped at `limit`, so the merge reads at most
    `4 × limit` rows to return `limit`. A `UNION ALL` would be one round trip but would force four
    unrelated row shapes into one column list, and the shapes are what a client renders from.

    **Undated completions are excluded, not backdated.** `Topic.completedAt` is null for 27 of the 53
    completed topics in this database — the same gap Decision P works around for mastery — and an
    evidence list is a timeline. An item with no date has nowhere to go on it, and substituting
    `updatedAt` would place the learner's work on the day a row was last touched by anything.

    Scoped through `Module → Course.userId` and `StudySession.userId`, so another learner's course id
    returns an empty list.
    """
    from src.domains.knowledge.db_models import TopicCheckAttempt, TopicSection
    from src.domains.progress.db_models import StudySession

    factory = get_session_factory()
    async with factory() as session:
        topic_rows = (
            await session.execute(
                select(Topic.id, Topic.title, Module.title, Topic.completed_at)
                .select_from(Topic)
                .join(Module, Module.id == Topic.module_id)
                .join(Course, Course.id == Module.course_id)
                .where(
                    Course.id == course_id,
                    Course.user_id == user_id,
                    Topic.completed.is_(True),
                    Topic.completed_at.is_not(None),
                )
                .order_by(Topic.completed_at.desc())
                .limit(limit)
            )
        ).all()

        section_rows = (
            await session.execute(
                select(TopicSection.id, TopicSection.title, Topic.title, TopicSection.completed_at)
                .select_from(TopicSection)
                .join(Topic, Topic.id == TopicSection.topic_id)
                .join(Module, Module.id == Topic.module_id)
                .join(Course, Course.id == Module.course_id)
                .where(
                    Course.id == course_id,
                    Course.user_id == user_id,
                    TopicSection.completed.is_(True),
                    TopicSection.completed_at.is_not(None),
                )
                .order_by(TopicSection.completed_at.desc())
                .limit(limit)
            )
        ).all()

        session_rows = (
            await session.execute(
                select(
                    StudySession.id,
                    StudySession.start_time,
                    StudySession.duration,
                )
                .where(
                    StudySession.user_id == user_id,
                    StudySession.course_id == course_id,
                )
                .order_by(StudySession.start_time.desc())
                .limit(limit)
            )
        ).all()

        # First attempt per topic, as elsewhere: the answer key ships to the browser, so a later
        # attempt is not evidence of knowing.
        first_attempts = (
            select(
                TopicCheckAttempt.topic_id.label("topic_id"),
                func.min(TopicCheckAttempt.created_at).label("first_at"),
            )
            .where(TopicCheckAttempt.user_id == user_id)
            .group_by(TopicCheckAttempt.topic_id)
            .subquery()
        )
        check_rows = (
            await session.execute(
                select(
                    TopicCheckAttempt.id,
                    Topic.title,
                    TopicCheckAttempt.correct,
                    TopicCheckAttempt.created_at,
                )
                .select_from(TopicCheckAttempt)
                .join(
                    first_attempts,
                    (first_attempts.c.topic_id == TopicCheckAttempt.topic_id)
                    & (first_attempts.c.first_at == TopicCheckAttempt.created_at),
                )
                .join(Topic, Topic.id == TopicCheckAttempt.topic_id)
                .join(Module, Module.id == Topic.module_id)
                .join(Course, Course.id == Module.course_id)
                .where(Course.id == course_id, Course.user_id == user_id)
                .order_by(TopicCheckAttempt.created_at.desc())
                .limit(limit)
            )
        ).all()

    items: list[EvidenceItem] = []

    for topic_id, title, module_title, completed_at in topic_rows:
        items.append(
            EvidenceItem(
                id=f"topic:{topic_id}",
                kind="topic_completed",
                title=title,
                detail=module_title,
                occurred_at=completed_at,
            )
        )

    for section_id, title, topic_title, completed_at in section_rows:
        items.append(
            EvidenceItem(
                id=f"section:{section_id}",
                kind="section_completed",
                title=title,
                detail=topic_title,
                occurred_at=completed_at,
            )
        )

    for study_id, start_time, duration in session_rows:
        minutes = float(duration or 0.0)
        items.append(
            EvidenceItem(
                id=f"session:{study_id}",
                kind="study_session",
                title="Study session",
                detail=None,
                occurred_at=start_time,
                # `StudySession.duration` is minutes, unlike `QuizSession.durationSeconds`.
                value=round(minutes, 1),
                unit="min",
            )
        )

    for attempt_id, topic_title, correct, created_at in check_rows:
        items.append(
            EvidenceItem(
                id=f"check:{attempt_id}",
                kind="knowledge_check",
                title=topic_title,
                detail=None,
                occurred_at=created_at,
                correct=bool(correct),
            )
        )

    items.sort(key=lambda item: item.occurred_at, reverse=True)
    return items[:limit]


async def list_prep_evidence(
    *, user_id: str, prep_id: str, limit: int = 12
) -> list[EvidenceItem]:
    """Recent dated evidence for one exam preparation, newest first.

    The sibling of `list_course_evidence`, and it exists because `ExamPrep` has no join to `Course`
    anywhere: `QuizSession.prepId` points at `ExamPrep` and `QuizSession.topicId` at `PrepTopic`, which
    is a different table from the knowledge `Topic` the course reader walks. A goal linked to a
    preparation was returning an empty panel while the learner had done real work.

    **Two sources, and the split between them is the design.**

    A *completed* quiz session is one evidence item carrying its own score. An *incomplete* one has no
    score and no completion instant, but its individual answers are recorded in `PracticeObservation`
    with a real `observedAt` and a server-graded `isCorrect` — so the answers stand in for a session
    that never closed. In this database that is most of the work: of 104 sessions only 13 ever reached
    `completedAt`, while 83 observations exist.

    **An observation belonging to a completed session is deliberately excluded.** Its session is already
    published above, with a score summarising exactly those answers, so including both would list one
    five-question quiz as six rows and push everything older off the panel. The rule is stated in terms
    of the session's own state rather than "whatever we already emitted", so it does not change meaning
    with `limit`.

    Not built here, each for a reason:

    - **`PrepTopic` reaching `MASTERED`.** That table has no `completedAt`, so the only available date is
      `updatedAt` — the same substitution `list_course_evidence` refuses for undated topic completions,
      because it dates the learner's work to whenever a row was last touched. Live, exactly one topic in
      the whole database is `MASTERED`, so the rule costs one row and keeps the timeline honest.
    - **`PrepReadinessSnapshot`.** It is a daily state capture, not an event. A list of "readiness was
      recorded" rows is volume without information.
    - **`ExamPrep.examDate`.** Nothing here reads it. That column is `timestamp without time zone` in
      Postgres while the ORM declares `DateTime(timezone=True)`, which is precisely the mismatch that
      made `GET /progress/goals` return a 500 for every goal with a target date.
    """
    from src.domains.personal_learning.db_models import (
        PracticeObservation,
        PrepTopic,
        QuizSession,
    )

    factory = get_session_factory()
    async with factory() as session:
        # Both scoping columns sit on the row itself — `QuizSession` carries `userId` as well as
        # `prepId` — so a foreign prep id returns nothing without needing a join to prove it.
        quiz_rows = (
            await session.execute(
                select(
                    QuizSession.id,
                    PrepTopic.title,
                    QuizSession.completed_at,
                    QuizSession.score_percentage,
                )
                .select_from(QuizSession)
                .outerjoin(PrepTopic, PrepTopic.id == QuizSession.topic_id)
                .where(
                    QuizSession.user_id == user_id,
                    QuizSession.prep_id == prep_id,
                    QuizSession.completed_at.is_not(None),
                )
                .order_by(QuizSession.completed_at.desc())
                .limit(limit)
            )
        ).all()

        answer_rows = (
            await session.execute(
                select(
                    PracticeObservation.id,
                    PrepTopic.title,
                    PracticeObservation.observed_at,
                    PracticeObservation.is_correct,
                )
                .select_from(PracticeObservation)
                .join(QuizSession, QuizSession.id == PracticeObservation.quiz_session_id)
                .outerjoin(PrepTopic, PrepTopic.id == PracticeObservation.prep_topic_id)
                .where(
                    PracticeObservation.user_id == user_id,
                    PracticeObservation.prep_id == prep_id,
                    QuizSession.completed_at.is_(None),
                )
                .order_by(PracticeObservation.observed_at.desc())
                .limit(limit)
            )
        ).all()

    items: list[EvidenceItem] = []

    for quiz_id, topic_title, completed_at, score in quiz_rows:
        items.append(
            EvidenceItem(
                id=f"quiz:{quiz_id}",
                kind="quiz_session",
                title="Practice quiz",
                # The topic when the quiz was scoped to one. The preparation's own subject is not
                # repeated: this panel is already scoped to the goal that points at it.
                detail=topic_title,
                occurred_at=completed_at,
                # `None` stays `None`. A completed session with no recorded score is unmeasured, and
                # publishing `0.0` would read as every answer wrong (Decision I).
                value=None if score is None else round(float(score), 1),
                unit=None if score is None else "%",
            )
        )

    for observation_id, topic_title, observed_at, is_correct in answer_rows:
        items.append(
            EvidenceItem(
                id=f"practice:{observation_id}",
                kind="practice_answer",
                title=topic_title or "Practice question",
                detail=None,
                occurred_at=observed_at,
                # Server-graded and not null on this table, unlike the quiz score.
                correct=bool(is_correct),
            )
        )

    # After the merge, so the newest of one kind cannot crowd out newer items of the other.
    items.sort(key=lambda item: item.occurred_at, reverse=True)
    return items[:limit]


async def list_goal_evidence(*, user_id: str, goal, limit: int = 12) -> list[EvidenceItem]:
    """Evidence for a goal, through whatever the goal is actually linked to.

    A goal is not itself measurable — it points at a course, a topic or a preparation, and the evidence
    is the work done on that thing. So this resolves the link and delegates.

    **A goal with no link has no evidence, and returns an empty list rather than the learner's general
    activity.** One of the six goals in this database is exactly that: `metricKind = manual` with no
    `courseId`, `topicId` or `prepId`. Falling back to everything the learner did would attach unrelated
    work to a goal and make the panel look informative while being wrong.

    `topicId` resolves to its course, because evidence for "finish this topic" sensibly includes the
    sessions and checks around it, and the per-topic tables are too thin to fill a panel on their own.

    `prepId` goes to `list_prep_evidence` rather than resolving to a course, because `ExamPrep` has no
    join to `Course` anywhere (§7.2) — the prep tables are a separate body of evidence, not a view of
    the course ones. The course link is checked first: a goal carrying both is a goal about a course
    that happens to have a preparation attached, and the course reader covers the wider ground.
    """
    course_id = getattr(goal, "course_id", None)

    if course_id is None and not getattr(goal, "topic_id", None):
        prep_id = getattr(goal, "prep_id", None)
        if prep_id:
            return await list_prep_evidence(user_id=user_id, prep_id=prep_id, limit=limit)

    if course_id is None and getattr(goal, "topic_id", None):
        factory = get_session_factory()
        async with factory() as session:
            course_id = (
                await session.execute(
                    select(Module.course_id)
                    .select_from(Topic)
                    .join(Module, Module.id == Topic.module_id)
                    .join(Course, Course.id == Module.course_id)
                    .where(Topic.id == goal.topic_id, Course.user_id == user_id)
                    .limit(1)
                )
            ).scalar_one_or_none()

    if course_id is None:
        return []

    return await list_course_evidence(user_id=user_id, course_id=course_id, limit=limit)


# ---------------------------------------------------------------------------
# Growth milestones
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrowthMilestone:
    """One thing the learner unlocked, from either milestone table.

    **Two inputs, one published list — and that is the point.** Decision Q settled that Reflect should
    have a single milestone source and named `Achievement`. Auditing the data showed that choice landed
    on the table nothing writes: `create_achievement` is called from nowhere in `src`, and the four rows
    that exist are Prisma-era records belonging to one learner. The table that is *actually* written is
    `LearningMilestone`, via `milestone_service._record_milestone`, and it has rows for five learners.

    Building on `Achievement` alone would ship a panel that is permanently empty for almost everyone.
    Building on `LearningMilestone` alone would drop four real records from the one learner who has
    them. So both are read and normalised into one ordered list: Decision Q's concern was two lists
    that could disagree, and there is one list here. Its *choice* of table is what needed correcting,
    and that correction is recorded rather than made quietly.
    """

    id: str
    #: `achievementType` for a legacy row, `conditionType` for a milestone. Kept because the client
    #: groups and colours by it.
    kind: str
    title: str
    description: str | None
    icon: str | None
    unlocked_at: datetime
    #: Which table this came from, so a reader can tell live records from frozen ones.
    source: Literal["milestone", "achievement"]

    def __post_init__(self) -> None:
        """Normalise `unlocked_at`, for the same reason `EvidenceItem` does.

        This list is merged from two tables that disagree: `Achievement.unlockedAt` carries no offset
        while `LearningMilestone` does. Sorting them together would raise `TypeError` for the first
        learner holding rows in both — nobody does yet, which is exactly what makes it worth fixing now
        rather than when it happens.
        """
        object.__setattr__(self, "unlocked_at", ensure_utc(self.unlocked_at))


async def list_growth_milestones(
    *,
    user_id: str,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
) -> list[GrowthMilestone]:
    """Milestones and legacy achievements, newest first, optionally windowed.

    `since`/`until` are inclusive **dates**, compared against the unlock instant's date, so a caller can
    scope the list to the same range as a trend chart without the two disagreeing about the edges.

    The catalogue lookup for a `LearningMilestone` is by `milestoneId` against `milestone_service`'s
    `MILESTONES`, which is where the title, description and icon live. A row whose id is not in the
    catalogue still appears — falling back to the raw id — because it happened, and hiding it would make
    a retired milestone look like it never occurred.
    """
    from src.domains.personal_learning.db_models import LearningMilestone
    from src.domains.progress.db_models import Achievement

    from .milestone_service import MILESTONES

    catalogue = {entry["id"]: entry for entry in MILESTONES}

    factory = get_session_factory()
    async with factory() as session:
        milestone_rows = (
            (
                await session.execute(
                    select(LearningMilestone)
                    .where(LearningMilestone.user_id == user_id)
                    .order_by(LearningMilestone.achieved_at.desc())
                )
            )
            .scalars()
            .all()
        )
        achievement_rows = (
            (
                await session.execute(
                    select(Achievement)
                    .where(Achievement.user_id == user_id)
                    .order_by(Achievement.unlocked_at.desc())
                )
            )
            .scalars()
            .all()
        )

    items: list[GrowthMilestone] = []

    for row in milestone_rows:
        entry = catalogue.get(row.milestone_id)
        items.append(
            GrowthMilestone(
                id=f"milestone:{row.id}",
                kind=(entry or {}).get("condition_type") or row.milestone_id,
                title=(entry or {}).get("title") or row.milestone_id,
                description=(entry or {}).get("description"),
                icon=(entry or {}).get("icon"),
                unlocked_at=row.achieved_at,
                source="milestone",
            )
        )

    for row in achievement_rows:
        items.append(
            GrowthMilestone(
                id=f"achievement:{row.id}",
                kind=row.achievement_type,
                title=row.title,
                description=row.description,
                icon=row.icon,
                unlocked_at=row.unlocked_at,
                source="achievement",
            )
        )

    if since is not None or until is not None:
        def in_window(item: GrowthMilestone) -> bool:
            day = item.unlocked_at.date()
            if since is not None and day < since:
                return False
            if until is not None and day > until:
                return False
            return True

        items = [item for item in items if in_window(item)]

    items.sort(key=lambda item: item.unlocked_at, reverse=True)
    return items[:limit] if limit else items
