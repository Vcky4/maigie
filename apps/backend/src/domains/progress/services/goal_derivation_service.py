"""Turn intent a learner has already stated into a goal that measures itself.

A learner who has enrolled in a course or set up an exam preparation has stated what they are
working towards. Until now none of that produced a `Goal`, so the goals surface was empty for
**593 of the 1167 learners who have no goal at all** while the very same database held their
course, their preparation and their study plan. This module closes that gap.

Three rules shape everything here.

**Only intent that can be measured becomes a goal.** `LearningProfile.purpose` and `goalsText` are
prose. A goal derived from prose could only be `metricKind='manual'`, and a manual goal's value is
whatever was written into it — nothing measures it, so it would sit at its birth number forever
while the learner worked. That is a goal in name only, and shipping one would be exactly the
false implementation this backend refuses. Courses and preparations are different: they carry the
`courseId` / `prepId` links that `goal_metrics.derive_current_values` already knows how to
measure, so a goal built on them reports real movement from the day it exists. Prose intent is
therefore used to *word* a goal, never to justify one.

**Nothing is derived for a link that already has a goal.** The check is per link and deliberately
ignores who created it and what state it is in: an ACTIVE learner-written goal, an ARCHIVED one,
or a previously derived one all count. A learner who set their own course goal must never find a
second one beside it wearing the same course.

**Derivation fires when intent is recorded, not on a schedule.** `progress_repo.delete_goal` is a
hard `DELETE`, so a nightly sweep over "courses without goals" would resurrect a goal the learner
deliberately threw away, every night, with no way for them to make it stop. Existing learners are
covered by a one-off backfill instead; see `derive_for_users`.

On targets: a course goal targets 100 percent, because completing a course is what enrolling in
one means. A preparation goal takes `ExamPrep.targetReadiness` and, when the learner never stated
one, leaves the target **empty rather than guessed** — the same choice migration 016 made for the
prep workspace, whose comment reads "without a stated target the workspace shows readiness with no
target line, rather than a guessed one". Neither kind needs a target to report progress: for
`course_progress` and `prep_readiness` the measured value is itself a percentage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.shared.database.session import get_session_factory
from src.shared.time.stored_instants import ensure_utc_optional

logger = logging.getLogger(__name__)

#: Both derivable kinds are measured on a 0-100 scale: `Course.progress` is a percentage (live
#: range 0-100) and `prep_readiness.progress_percent` is `topicsStrong / topicsTotal` as a percent.
FULL_PERCENT = 100.0

#: Preparation statuses worth a goal. A `COMPLETED` preparation is a finished piece of work, and a
#: goal to get ready for it would open already satisfied.
PREP_STATUSES_WORTH_A_GOAL = frozenset({"SETUP", "IN_PROGRESS"})

#: Tokens naming what a derived goal was built from. The client writes the sentence; this is the
#: same split the agenda's `placement` and the goals greeting `headline` use.
BASIS_COURSE = "course"
BASIS_PREPARATION = "preparation"

#: How many course goals a single unscoped derivation will create.
#:
#: One live learner has sixteen unarchived courses, two of them the same course twice. Deriving a
#: goal for each would put sixteen commitments on a surface whose job is to show what the learner is
#: working towards, which is the course list they already have, sorted worse. A goal is something
#: being actively pursued, so the cap takes the courses they have actually touched, most recently
#: updated first. Preparations are not capped: each carries a date the learner chose, and they are
#: few (46 across the whole database).
MAX_DERIVED_COURSE_GOALS = 3


@dataclass(frozen=True)
class DerivedGoalSpec:
    """A goal that *should* exist, before anything is written.

    Separating the decision from the write is what makes a preview endpoint possible: the same
    function answers "what would you create for me" and "create it", so the two can never disagree.
    """

    title: str
    description: str
    metric_kind: str
    unit: str
    #: `course` | `preparation`. A token, not a sentence.
    basis: str
    course_id: str | None = None
    prep_id: str | None = None
    target_value: float | None = None
    target_date: datetime | None = None

    def to_create_data(self) -> dict[str, Any]:
        """The camelCase payload `goal_service.create_goal` takes.

        `currentValue` is deliberately absent: `create_goal` refuses one on any goal that is not
        `manual`, because the figure is measured from the source that holds it. `progress` is absent
        for the same reason — it is derived from the measurement.
        """
        data: dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "metricKind": self.metric_kind,
            "unit": self.unit,
        }
        if self.target_value is not None:
            data["targetValue"] = self.target_value
        if self.target_date is not None:
            data["targetDate"] = self.target_date
        if self.course_id:
            data["courseId"] = self.course_id
        if self.prep_id:
            data["prepId"] = self.prep_id
        return data


async def plan_derivations(
    user_id: str,
    *,
    now: datetime | None = None,
    course_id: str | None = None,
    prep_id: str | None = None,
) -> list[DerivedGoalSpec]:
    """What this learner's stated intent implies, minus anything already covered by a goal.

    Reads nothing but the learner's own rows, in three queries in one session. Writes nothing, so a
    client may call it to ask before acting.

    `course_id` / `prep_id` narrow it to one piece of intent, which is what the paths that *record*
    intent pass: creating one course should propose a goal for that course, not quietly also open
    goals for three older ones the learner never asked about. Unscoped — the backfill and the
    endpoint — it covers the backlog, capped at `MAX_DERIVED_COURSE_GOALS` courses.
    """
    from src.domains.knowledge.db_models import Course
    from src.domains.personal_learning.db_models import ExamPrep
    from src.domains.progress.db_models import Goal

    moment = now or datetime.now(UTC)
    factory = get_session_factory()

    async with factory() as session:
        # Every goal the learner has, whatever its status and whichever space it belongs to.
        # `goal_service.list_goals` forces `spaceId IS NULL` when no space is asked for, so using it
        # here would miss a space-scoped goal and then duplicate it.
        linked = (
            await session.execute(
                select(Goal.course_id, Goal.prep_id).where(Goal.user_id == user_id)
            )
        ).all()
        taken_courses = {course_id for course_id, _ in linked if course_id}
        taken_preps = {prep_id for _, prep_id in linked if prep_id}

        prep_stmt = select(
            ExamPrep.id,
            ExamPrep.subject,
            ExamPrep.exam_date,
            ExamPrep.target_readiness,
            ExamPrep.prep_type,
        ).where(ExamPrep.user_id == user_id, ExamPrep.status.in_(PREP_STATUSES_WORTH_A_GOAL))
        if prep_id is not None:
            prep_stmt = prep_stmt.where(ExamPrep.id == prep_id)
        preps = [] if course_id is not None else (await session.execute(prep_stmt)).all()

        # Courses the learner has actually touched come first, then the most recently updated. When
        # the cap bites, it keeps what is being worked on rather than whatever sorted first by id.
        course_stmt = (
            select(Course.id, Course.title, Course.target_date, Course.progress)
            .where(Course.user_id == user_id, Course.archived.is_(False))
            .order_by((Course.progress > 0).desc(), Course.updated_at.desc())
        )
        if course_id is not None:
            course_stmt = course_stmt.where(Course.id == course_id)
        courses = [] if prep_id is not None else (await session.execute(course_stmt)).all()

    specs: list[DerivedGoalSpec] = []

    # Preparations first: they carry a date the learner chose, which makes them the more urgent
    # commitment of the two.
    for row_prep_id, subject, exam_date, target_readiness, prep_type in preps:
        if row_prep_id in taken_preps:
            continue
        # `examDate` is one of the 176 columns stored without an offset, so it must never be
        # compared raw against an aware instant.
        due = ensure_utc_optional(exam_date)
        if due is not None and due < moment:
            # The date has passed. Getting ready for it is no longer a goal.
            continue
        specs.append(
            DerivedGoalSpec(
                title=f"Be ready for {subject}",
                description=_prep_description(subject=subject, prep_type=prep_type),
                metric_kind="prep_readiness",
                unit="percent readiness",
                basis=BASIS_PREPARATION,
                prep_id=row_prep_id,
                # Left as stated, `None` included. Never guessed.
                target_value=float(target_readiness) if target_readiness is not None else None,
                target_date=due,
            )
        )

    course_specs: list[DerivedGoalSpec] = []
    for row_course_id, title, target_date, progress in courses:
        if len(course_specs) >= MAX_DERIVED_COURSE_GOALS:
            break
        if row_course_id in taken_courses:
            continue
        if float(progress or 0.0) >= FULL_PERCENT:
            # Already finished. A goal to complete it would open satisfied.
            continue
        course_specs.append(
            DerivedGoalSpec(
                title=f"Complete {title}",
                description=(
                    f"Finish {title}. Progress tracks the course itself, so it moves as you "
                    "complete topics."
                ),
                metric_kind="course_progress",
                unit="percent complete",
                basis=BASIS_COURSE,
                course_id=row_course_id,
                target_value=FULL_PERCENT,
                target_date=ensure_utc_optional(target_date),
            )
        )

    return specs + course_specs


def _prep_description(*, subject: str, prep_type: str | None) -> str:
    kind = (prep_type or "").strip().lower()
    noun = kind if kind in {"exam", "certification", "interview", "test"} else "preparation"
    return (
        f"Get ready for your {subject} {noun}. Readiness is measured from the topics you have "
        "made strong, so this moves as you practise."
    )


async def derive_goals_for_user(
    user_id: str,
    *,
    now: datetime | None = None,
    course_id: str | None = None,
    prep_id: str | None = None,
) -> list[Any]:
    """Create the goals this learner's stated intent implies. Idempotent.

    Returns the goals created, which is empty when everything is already covered — the ordinary
    result on every call after the first.
    """
    specs = await plan_derivations(user_id, now=now, course_id=course_id, prep_id=prep_id)
    if not specs:
        return []

    from src.domains.progress.services import goal_service

    created: list[Any] = []
    for spec in specs:
        goal = await goal_service.create_goal(user_id=user_id, data=spec.to_create_data())
        created.append(goal)

    logger.info(
        "Derived %d goal(s) from stated intent",
        len(created),
        extra={"user_id": user_id, "bases": [spec.basis for spec in specs]},
    )
    return created


async def derive_goals_quietly(
    user_id: str, *, course_id: str | None = None, prep_id: str | None = None
) -> list[Any]:
    """`derive_goals_for_user` for a caller whose own work must not fail with it.

    Used from the paths that record intent — creating a course, creating a preparation. A learner
    who has just created a course has had that course created; failing the request because the goal
    beside it could not be written would throw away work they did.

    Those callers pass the id of the thing they just created, so creating one course opens a goal for
    that course and nothing else.
    """
    try:
        return await derive_goals_for_user(user_id, course_id=course_id, prep_id=prep_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Goal derivation failed for user %s: %s", user_id, exc, extra={"user_id": user_id}
        )
        return []


async def derive_for_users(user_ids: list[str]) -> dict[str, int]:
    """Backfill, for the learners who stated their intent before this existed.

    Sequential on purpose. The session-mode pooler allows roughly fifteen clients, and fanning out
    with `asyncio.gather` across sessions is what took `daily-counts` down.

    Deliberately **not** on the beat schedule: goal deletion is a hard delete, so a recurring sweep
    would rebuild a goal the learner removed, nightly, forever.
    """
    counts: dict[str, int] = {}
    for user_id in user_ids:
        created = await derive_goals_quietly(user_id)
        if created:
            counts[user_id] = len(created)
    return counts
