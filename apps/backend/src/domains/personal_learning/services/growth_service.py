"""Growth trends and subject mastery, read from the daily snapshot.

The read side of `DailyLearningSnapshot`. Every figure here is a *stored* measurement or a difference
between two of them; nothing is computed from live state, because a trend whose most recent point
came from a different source than the rest would show a step at the join.

**Two rules govern this whole module, and both are about what is *not* returned.**

A day with no snapshot is **absent from the series**. It is not zero-filled and the previous value is
not carried forward. The learner was not observed that day; a zero asserts they failed and a carried
value asserts a measurement nobody took. The client draws no point and says so.

A range above the learner's plan returns a **locked notice with an empty series, not an error**. The
design renders three toggles and Free must be able to press the third: a `403` makes the control look
broken, and silently serving 30 days as though they were 90 is a lie told in a chart (Decision T).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.shared.exceptions import NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)

#: The capability these ranges are gated by, as registered in `FEATURE_TIER_MATRIX`.
_TREND_CAPABILITY = "behaviour_analytics"


def _range_days(range_: str) -> int:
    return models.GROWTH_RANGE_DAYS[range_]


def _delta(values: list[float]) -> models.GrowthDelta:
    """First against last, over the days that actually hold a value.

    `None` throughout when fewer than two days were captured. A single observation is not a trend,
    and publishing `change: 0` from one point would claim a flat line that was never measured.
    """
    if len(values) < 2:
        return models.GrowthDelta(
            first=values[0] if values else None,
            last=values[0] if values else None,
            change=None,
        )
    return models.GrowthDelta(
        first=values[0], last=values[-1], change=round(values[-1] - values[0], 1)
    )


def _point(snapshot: Any) -> models.GrowthTrendPoint:
    return models.GrowthTrendPoint(
        day=snapshot.snapshot_date,
        focused_minutes=snapshot.focused_minutes,
        effort_score=snapshot.effort_score,
        consistency_score=snapshot.consistency_score,
        mastery_percent=snapshot.overall_mastery_percent,
        cards_reviewed=snapshot.cards_reviewed or 0,
        recall_percent=snapshot.recall_percent,
        topics_completed=snapshot.topics_completed or 0,
        active_day=bool(snapshot.active_day),
        reconstructed=bool(snapshot.reconstructed),
    )


async def _locked_notice(user_id: str, range_: str) -> models.LockedNotice | None:
    """The notice for a range above this learner's plan, or `None` when they may read it.

    Reads the same `feature_tier_service` every other gate reads, so a learner on a trial gets the
    long range here exactly as they do everywhere else.
    """
    if range_ in models.FREE_GROWTH_RANGES:
        return None

    from . import feature_tier_service

    if await feature_tier_service.get_quality_tier(user_id) == "plus":
        return None

    matrix = feature_tier_service.FEATURE_TIER_MATRIX.get(_TREND_CAPABILITY, {})
    return models.LockedNotice(
        reason=f"The {range_} history range requires Maigie Plus",
        capability=_TREND_CAPABILITY,
        trial_available=await feature_tier_service.trial_available(user_id),
        upgrade_value=matrix.get("upgrade_value", ""),
    )


def _window(range_: str, *, now: datetime | None = None) -> tuple[date, date, int]:
    """`(since, until, days)` for a range, as learner-agnostic UTC dates.

    **Ends at yesterday, not today**, because that is the newest day the snapshot can hold: the
    nightly writer records each learner's most recently *finished* local day, since a day still in
    progress is not a measurement. Ending the window at today asked for a row that by definition does
    not exist yet and made every range report one fewer captured day than it had — a 7-day chart
    showing six points and a "6 of 7" that looked like a gap in the data rather than the shape of the
    schedule.

    Deliberately **not** resolved through the learner's timezone. `snapshotDate` was already written
    in their local calendar by the writer, so bounding the read by their zone as well would shift the
    window a second time and drop or duplicate an edge day.
    """
    days = _range_days(range_)
    until = (now or datetime.now(UTC)).date() - timedelta(days=1)
    return until - timedelta(days=days - 1), until, days


def to_growth_milestone(item) -> models.GrowthMilestone:
    """Map a `GrowthMilestone` dataclass onto the wire. Shared by the trends read and the dashboard."""
    return models.GrowthMilestone(
        id=item.id,
        kind=item.kind,
        title=item.title,
        description=item.description,
        icon=item.icon,
        unlocked_at=item.unlocked_at,
        source=item.source,
    )


async def _range_milestones(
    *, user_id: str, since: date, until: date
) -> list[models.GrowthMilestone]:
    """What the learner unlocked inside the trend window.

    Scoped to the same `since`/`until` as `points`, so the list beneath the chart cannot describe a
    different period from the chart itself.

    Failures degrade to an empty list rather than propagating: the three series are the point of this
    response, and losing them because a milestone read failed would be the wrong trade — the same
    reasoning `_subject_activity` follows.
    """
    from . import reflect_aggregates

    try:
        items = await reflect_aggregates.list_growth_milestones(
            user_id=user_id, since=since, until=until
        )
    except Exception as exc:  # noqa: BLE001 — the series must survive a milestone failure
        logger.warning("Growth milestones unavailable for user %s: %s", user_id, exc)
        return []
    return [to_growth_milestone(item) for item in items]


async def get_trends(
    *, user_id: str, range_: str = "30d", now: datetime | None = None
) -> models.GrowthTrendsResponse:
    """The three growth series across a bounded range, plus the change in each.

    Mastery, consistency and effort — three distinct signals rather than one rendered three times,
    which is the whole reason `effortScore` is not focused minutes (Decision U).
    """
    since, until, days = _window(range_, now=now)

    locked = await _locked_notice(user_id, range_)
    if locked is not None:
        # An empty series with the notice attached. Not a shorter range wearing this one's label.
        return models.GrowthTrendsResponse(range=range_, days=days, locked=locked)

    snapshots = await repo.list_daily_snapshots(user_id, since=since, until=until)
    points = [_point(snapshot) for snapshot in snapshots]
    milestones = await _range_milestones(user_id=user_id, since=since, until=until)

    return models.GrowthTrendsResponse(
        range=range_,
        days=days,
        points=points,
        milestones=milestones,
        mastery=_delta([p.mastery_percent for p in points if p.mastery_percent is not None]),
        consistency=_delta(
            [p.consistency_score for p in points if p.consistency_score is not None]
        ),
        effort=_delta([p.effort_score for p in points if p.effort_score is not None]),
        captured_days=len(points),
        reconstructed_days=sum(1 for p in points if p.reconstructed),
        active_days=sum(1 for p in points if p.active_day),
    )


async def _subject_changes(*, user_id: str, since: date, until: date) -> dict[str, float]:
    """Percentage points each course moved across the window, from the snapshot's `subjectMastery`.

    Differences the **earliest and latest snapshots that actually carry a mastery dict**, rather than
    the window's first and last days. A reconstruction that could not date the learner's completions
    stores `subjectMastery` as null (Decision P), and treating that null as zero would report every
    subject as having gained its entire current mastery inside the range.

    Courses absent from either end are omitted, so their `change` stays `None` rather than being
    invented from a single observation.
    """
    snapshots = await repo.list_daily_snapshots(user_id, since=since, until=until)
    with_mastery = [s for s in snapshots if s.subject_mastery]
    if len(with_mastery) < 2:
        return {}

    first, last = with_mastery[0].subject_mastery, with_mastery[-1].subject_mastery
    return {
        course_id: round(float(value) - float(first[course_id]), 1)
        for course_id, value in last.items()
        if course_id in first
    }


def _to_activity_summary(activity) -> models.SubjectActivitySummary | None:
    """Map a `SubjectActivity` onto the wire.

    `None` only when the activity read itself failed, which is why callers go through
    `SubjectActivityMap.for_course` rather than `dict.get`: a course with no row still needs a row on
    the wire, correctly nulled or correctly zeroed depending on whether the learner tracks time at all.
    Returning `None` for "no sessions on this subject" would show a dash where `0` is the truth.
    """
    if activity is None:
        return None
    return models.SubjectActivitySummary(
        sessions=activity.sessions,
        focused_minutes=activity.focused_minutes,
        active_days=activity.active_days,
        knowledge_checks_answered=activity.knowledge_checks_answered,
        knowledge_check_accuracy_percent=activity.knowledge_check_accuracy_percent,
    )


def _to_evidence_item(item) -> models.EvidenceItem:
    """Map an `EvidenceItem` dataclass onto the wire. Shared by the subject and goal reads."""
    return models.EvidenceItem(
        id=item.id,
        kind=item.kind,
        title=item.title,
        detail=item.detail,
        occurred_at=item.occurred_at,
        value=item.value,
        unit=item.unit,
        correct=item.correct,
    )


async def _subject_activity(*, user_id: str, since, until) -> dict:
    """Per-course activity for the window, resolved in the learner's own timezone.

    Its own helper because both subject reads need it and both must agree. Failures degrade to an empty
    map rather than propagating: activity is an addition to a response whose mastery half is independent,
    and losing the whole subjects list because `StudySession` could not be read would be the wrong
    trade — the same reasoning `_compose_narrative` uses for its skeleton.
    """
    from src.shared.time import resolve_learner_timezone

    from . import reflect_aggregates

    try:
        timezone_ = await resolve_learner_timezone(user_id)
        return await reflect_aggregates.list_subject_activity(
            user_id=user_id, since=since, until=until, timezone_=timezone_
        )
    except Exception as exc:  # noqa: BLE001 — mastery must survive an activity failure
        logger.warning("Subject activity unavailable for user %s: %s", user_id, exc)
        # `tracked_any_session=False` so every subject reads as unmeasured rather than as zero. A
        # failed read must not be published as "you did nothing".
        return reflect_aggregates.SubjectActivityMap(by_course={}, tracked_any_session=False)


async def get_subjects(
    *, user_id: str, range_: str = "30d", limit: int | None = None, now: datetime | None = None
) -> models.GrowthSubjectsResponse:
    """Mastery by subject, with the change across the range filled in.

    `SubjectMastery.change` has been `None` since Phase 2 with a comment saying it would stay that
    way until a daily snapshot existed. This is where it stops being null.
    """
    from . import reflect_aggregates

    since, until, days = _window(range_, now=now)
    subjects = await reflect_aggregates.list_subject_mastery(user_id=user_id, limit=limit)
    changes = await _subject_changes(user_id=user_id, since=since, until=until)
    activity = await _subject_activity(user_id=user_id, since=since, until=until)

    return models.GrowthSubjectsResponse(
        range=range_,
        days=days,
        items=[
            models.GrowthSubject(
                course_id=subject.course_id,
                title=subject.title,
                category=subject.category,
                mastery_percent=subject.mastery_percent,
                topics_total=subject.topics_total,
                topics_completed=subject.topics_completed,
                change=changes.get(subject.course_id),
                activity=_to_activity_summary(activity.for_course(subject.course_id)),
            )
            for subject in subjects
        ],
    )


async def get_subject_detail(
    *, user_id: str, course_id: str, range_: str = "30d", now: datetime | None = None
) -> models.GrowthSubjectDetailResponse:
    """One subject, with its own mastery series.

    Raises `NotFoundError` when the course is not the learner's. Scoped by reusing
    `list_subject_mastery`, which filters on `Course.userId` — so another learner's course id is
    indistinguishable from one that does not exist, rather than leaking its existence through a 403.
    """
    from . import reflect_aggregates

    since, until, days = _window(range_, now=now)
    subjects = await reflect_aggregates.list_subject_mastery(user_id=user_id)
    subject = next((s for s in subjects if s.course_id == course_id), None)
    if subject is None:
        raise NotFoundError("Course", course_id)

    snapshots = await repo.list_daily_snapshots(user_id, since=since, until=until)
    changes = await _subject_changes(user_id=user_id, since=since, until=until)
    activity = await _subject_activity(user_id=user_id, since=since, until=until)
    concepts = await reflect_aggregates.list_concept_mastery(user_id=user_id, course_id=course_id)
    evidence = await reflect_aggregates.list_course_evidence(user_id=user_id, course_id=course_id)

    # The per-subject series carries this course's mastery in `masteryPercent`, not the learner's
    # overall figure. Everything else on the point describes the day rather than the subject, since
    # nothing records cards or minutes per course.
    points: list[models.GrowthTrendPoint] = []
    for snapshot in snapshots:
        point = _point(snapshot)
        by_course = snapshot.subject_mastery or {}
        points.append(
            point.model_copy(
                update={
                    "mastery_percent": (
                        by_course.get(course_id) if snapshot.subject_mastery else None
                    )
                }
            )
        )

    return models.GrowthSubjectDetailResponse(
        subject=models.GrowthSubject(
            course_id=subject.course_id,
            title=subject.title,
            category=subject.category,
            mastery_percent=subject.mastery_percent,
            topics_total=subject.topics_total,
            topics_completed=subject.topics_completed,
            change=changes.get(course_id),
            activity=_to_activity_summary(activity.for_course(course_id)),
        ),
        range=range_,
        days=days,
        points=points,
        concepts=[
            models.SubjectConcept(
                topic_id=concept.topic_id,
                title=concept.title,
                mastery_percent=concept.mastery_percent,
                source=concept.source,
                sections_total=concept.sections_total,
                sections_completed=concept.sections_completed,
                completed=concept.completed,
                status=concept.status,
            )
            for concept in concepts
        ],
        evidence=[_to_evidence_item(item) for item in evidence],
    )


# ---------------------------------------------------------------------------
# The written interpretation (Decision Z)
# ---------------------------------------------------------------------------


async def get_drivers(
    *, user_id: str, range_: str = "30d", now: datetime | None = None
) -> models.GrowthDriversResponse:
    """What moved the curve, as prose over figures this module already measured.

    **Reads `get_trends` rather than the snapshots.** The drivers panel sits under the chart, so the
    two must agree about what changed; deriving the same deltas a second time would make that a
    coincidence rather than a guarantee. It is the rule `reflect_dashboard_service` follows for its
    summary ring.

    **Plus, delivered as a `200` with a `LockedNotice`** (Decision Z). Free keeps every figure on the
    page and loses only the interpretation, so the panel becomes an upgrade card rather than an error.

    Two locks can apply and the range one wins: a Free learner asking for 90 days has no series to
    write about, so the notice explains the range rather than the prose. Composing an interpretation of
    an empty series would be the more confusing answer.
    """
    from . import growth_narrative, narrative_cache

    trends = await get_trends(user_id=user_id, range_=range_, now=now)
    if trends.locked is not None:
        return models.GrowthDriversResponse(
            range=trends.range, days=trends.days, locked=trends.locked
        )

    locked = await narrative_cache.plus_gate(
        user_id, reason="Reading what drove your growth requires Maigie Plus"
    )
    if locked is not None:
        return models.GrowthDriversResponse(range=trends.range, days=trends.days, locked=locked)

    skeleton = growth_narrative.build_drivers(trends)
    if not skeleton:
        # Nothing moved measurably. An empty panel, and no generation spent asking a model to
        # explain a movement that was not observed.
        return models.GrowthDriversResponse(range=trends.range, days=trends.days)

    async def _compose() -> dict[str, Any] | None:
        return await narrative_cache.compose_json(
            user_id=user_id,
            prompt=growth_narrative.build_drivers_prompt(range_=range_, skeleton=skeleton),
            what="growth drivers",
        )

    written = await narrative_cache.resolve(
        user_id=user_id,
        kind="growth_drivers",
        scope=range_,
        inputs=skeleton,
        compose=_compose,
    )
    return models.GrowthDriversResponse(
        range=trends.range,
        days=trends.days,
        items=growth_narrative.assemble_drivers(skeleton=skeleton, written=written),
    )


async def get_subject_insight(
    *, user_id: str, course_id: str, range_: str = "30d", now: datetime | None = None
) -> models.SubjectInsightResponse:
    """One subject's strength, focus and next step.

    Raises `NotFoundError` for a course that is not the learner's, because it reads
    `get_subject_detail` — so ownership is checked by the same predicate that produces the figures,
    rather than by a second check that could drift from it.

    The next step's target and label are chosen here, from the measurement, before the model is asked
    for anything (Decision O). The chosen step's grounds are then put *into* the prompt so the focus
    paragraph argues for the button beneath it.
    """
    from . import growth_narrative, narrative_cache

    detail = await get_subject_detail(user_id=user_id, course_id=course_id, range_=range_, now=now)
    locked = await narrative_cache.plus_gate(
        user_id, reason="Subject insight and recommendations require Maigie Plus"
    )
    if locked is not None:
        return models.SubjectInsightResponse(course_id=course_id, range=detail.range, locked=locked)

    reason, label, target = growth_narrative.choose_next_step(detail)
    skeleton = growth_narrative.build_subject_skeleton(detail)
    # The chosen step is part of the fingerprint, not just of the prompt. The prose argues for the
    # step, so a subject whose figures moved it from "plan a session" to "revisit a topic" needs a new
    # paragraph even in the impossible case that every other figure held still.
    inputs = {"subject": skeleton, "step": {"reason": reason, "kind": target.kind.value}}

    async def _compose() -> dict[str, Any] | None:
        return await narrative_cache.compose_json(
            user_id=user_id,
            prompt=growth_narrative.build_subject_prompt(
                skeleton=skeleton, range_=range_, reason=reason
            ),
            what="subject insight",
        )

    written = await narrative_cache.resolve(
        user_id=user_id,
        kind="subject_insight",
        entity_id=course_id,
        scope=range_,
        inputs=inputs,
        compose=_compose,
    )
    insight, next_step = growth_narrative.assemble_subject(
        written=written, label=label, target=target, reason=reason
    )
    return models.SubjectInsightResponse(
        course_id=course_id, range=detail.range, insight=insight, next_step=next_step
    )
