"""Daily learning snapshots, and the trends they make possible.

Every Reflect trend — the growth curve, per-subject `change`, `masteryChange`,
`consistencyChange`, the library's monthly rhythm — is a question about the past, and the
values they trend are mutable in place. `Course.progress` is overwritten by
`recount_course_progress`; `LearningProfile.consistencyScore` is overwritten by the nightly
behaviour task. Yesterday's number is not archived anywhere, so a trend needs storage. This
module owns that storage: one row per learner per learner-local day.

`prep_snapshot_service` is the precedent and this deliberately mirrors its shape. The one place
it departs is the day boundary — see `snapshot_day_for`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.shared.time import LearnerTimezone, to_learner_local

logger = logging.getLogger(__name__)

# How far back a trend may be requested. The design offers 7, 30 and 90 days; the ceiling is
# the longest of those, and an unbounded range is an unbounded query.
MAX_TREND_DAYS = 90
DEFAULT_TREND_DAYS = 30

# Learners processed per batch by the nightly writer, so memory does not scale with the number
# of learners.
_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Effort
# ---------------------------------------------------------------------------

# The caps at which more of one input stops being evidence of a harder day. Named rather than
# inlined because they are the whole content of the metric: changing one changes what every
# stored `effortScore` means, so they should be hard to change by accident and easy to find.
EFFORT_MINUTES_CAP = 180.0
EFFORT_CARDS_CAP = 40.0
EFFORT_QUIZ_ANSWERS_CAP = 20.0
EFFORT_TOPICS_CAP = 3.0

# Weighted toward time because that is the input every learner produces, with retrieval work
# carrying most of the rest: a 40-card review session is real effort that three hours of
# reading does not subsume.
EFFORT_MINUTES_WEIGHT = 0.40
EFFORT_CARDS_WEIGHT = 0.30
EFFORT_QUIZ_ANSWERS_WEIGHT = 0.20
EFFORT_TOPICS_WEIGHT = 0.10


def compute_effort_score(
    *,
    focused_minutes: float | None,
    cards_reviewed: int,
    quiz_answers: int,
    topics_touched: int,
) -> float:
    """The volume of deliberate work on one day, 0-100.

    The third growth-curve series. It must not be focused minutes, or the chart renders one
    signal twice: effort is *how much deliberate work*, consistency is *how regularly*, and
    mastery is *the outcome*.

    **Absolute, with fixed caps, and not normalised against the learner's own trailing
    maximum** — which was the first instinct and is wrong. A moving denominator means a
    record-breaking day silently rewrites every earlier bar on the chart, so the learner
    watches their past shrink. A trend whose history changes is not a trend. Fixed caps are
    cruder and stable, and stability is the property a chart needs (Decision U).

    Returns `0.0` rather than `None` for a day with no qualifying work, and the distinction is
    deliberate. Absence and zero are kept apart everywhere else here — `recallPercent` is null
    when no card was reviewed, because nothing measured the learner's recall. Effort is not
    like that: it is *defined* as the volume of these four inputs, so no inputs is a measured
    zero, and a day off should read as a day off rather than as a gap in the record. `None`
    means there is no snapshot for that day at all.

    One function, used by both the nightly writer and the backfill. If they each had their own
    copy, a reconstructed day and a recorded day would eventually disagree about what effort
    means, and the chart would show a step where the two met.
    """
    minutes = max(0.0, float(focused_minutes or 0.0))

    return round(
        100.0
        * (
            EFFORT_MINUTES_WEIGHT * min(minutes / EFFORT_MINUTES_CAP, 1.0)
            + EFFORT_CARDS_WEIGHT * min(max(0, cards_reviewed) / EFFORT_CARDS_CAP, 1.0)
            + EFFORT_QUIZ_ANSWERS_WEIGHT * min(max(0, quiz_answers) / EFFORT_QUIZ_ANSWERS_CAP, 1.0)
            + EFFORT_TOPICS_WEIGHT * min(max(0, topics_touched) / EFFORT_TOPICS_CAP, 1.0)
        ),
        1,
    )


# ---------------------------------------------------------------------------
# The learner's day
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalDay:
    """One learner-local calendar day, and the UTC instants that bound it.

    Both halves are needed and neither is derivable from the other without the timezone:
    `day` is what gets stored and what the chart plots, while `start` and `end` are what the
    evidence queries filter on, since every source column is a UTC instant.
    """

    day: date
    start: datetime
    end: datetime


def snapshot_day_for(instant: datetime, timezone_: LearnerTimezone) -> date:
    """The learner's calendar date that a UTC instant fell on.

    **Not `instant.date()` and not `datetime.now(UTC).date()`.** A session at 23:30 in Lagos is
    the next day in UTC, so bucketing by UTC date either merges two of the learner's days into
    one or splits one of them across two — and `activeDay` and the rhythm chart are questions
    about the learner's own calendar, not about UTC's.

    `prep_snapshot_service._as_utc_date` does truncate to UTC, and `learner_timezone`'s module
    docstring records that as a known bug rather than as the pattern to copy.

    A learner whose timezone was never captured resolves to UTC through `UNKNOWN_TIMEZONE`.
    That is a limitation of their data, not a claim about where they are, and it is why
    `LearnerTimezone.is_known` exists for anything asserted back to them.
    """
    return to_learner_local(instant, timezone_).date()


def local_day_bounds(day: date, timezone_: LearnerTimezone) -> LocalDay:
    """The UTC window covering one of the learner's calendar days.

    Half-open at the end in effect: `end` is the last microsecond of the day, because the
    evidence queries this feeds use inclusive `<=` comparisons. Building the boundaries here
    rather than in each caller keeps the DST cases in one place — `astimezone` resolves them,
    and a day that is 23 or 25 hours long is still exactly one row.
    """
    start_local = datetime.combine(day, time.min, tzinfo=timezone_.zone)
    end_local = datetime.combine(day, time.max, tzinfo=timezone_.zone)
    return LocalDay(
        day=day,
        start=start_local.astimezone(UTC),
        end=end_local.astimezone(UTC),
    )


def recent_local_days(
    *, days: int, timezone_: LearnerTimezone, now: datetime | None = None
) -> list[LocalDay]:
    """The learner's last `days` calendar days, oldest first, ending today.

    Oldest first because a chart reads left to right, and because the backfill writes forward
    so a partial run leaves a contiguous history rather than a scatter.
    """
    reference = now or datetime.now(UTC)
    today = snapshot_day_for(reference, timezone_)
    span = max(1, days)
    return [
        local_day_bounds(today - timedelta(days=offset), timezone_)
        for offset in range(span - 1, -1, -1)
    ]


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def snapshot_values(
    evidence: Any,
    *,
    consistency_score: float | None,
    overall_mastery_percent: float | None,
    subject_mastery: dict[str, float] | None,
    reconstructed: bool = False,
) -> dict[str, Any]:
    """The fields one snapshot stores, keyed by Python attribute name for the upsert.

    Takes a `reflection_metrics.MetricEvidence` — typed as `Any` only to keep this module
    importable without pulling the evidence loader in, matching how `prep_snapshot_service`
    keeps its value builder free of query code.

    **Reads the primitive counters off the evidence rather than going through
    `reflection_metrics.compute`.** `compute` folds in `LearningProfile.consistencyScore`,
    `avgSessionMinutes` and `bestDayOfWeek`, which are *current* state rather than the state on
    the day being recorded. A backfill routed through it would stamp today's consistency score
    onto ninety historical rows and call them measurements.

    `consistency_score`, `overall_mastery_percent` and `subject_mastery` are therefore passed
    in by the caller, which is the only party that knows whether it is recording today or
    reconstructing a past day.
    """
    from .reflection_metrics import recall_percent

    cards_reviewed = int(evidence.reviews_total or 0)
    topics_touched = len(evidence.topics_touched or ())
    quiz_answers = int(evidence.quiz_answers_total or 0)
    focused_minutes = evidence.tracked_minutes

    # Anything at all that counts as showing up, including work no counter here measures —
    # a knowledge check attempt, a completed topic. `activity_instants` is the union the
    # evidence loader already assembles for exactly this question.
    active_day = bool(evidence.activity_instants)

    return {
        "focused_minutes": focused_minutes,
        "sessions_completed": int(evidence.study_sessions_ended or 0)
        + int(evidence.quizzes_completed or 0),
        "active_day": active_day,
        "consistency_score": consistency_score,
        "overall_mastery_percent": overall_mastery_percent,
        "cards_reviewed": cards_reviewed,
        # Null when no card was reviewed: nothing measured the learner's recall that day, and
        # `0.0` would draw a line at the bottom of the chart asserting total failure.
        "recall_percent": recall_percent(evidence),
        "topics_completed": len(evidence.topics_mastered or ()),
        "effort_score": compute_effort_score(
            focused_minutes=focused_minutes,
            cards_reviewed=cards_reviewed,
            quiz_answers=quiz_answers,
            topics_touched=topics_touched,
        ),
        "subject_mastery": subject_mastery,
        "reconstructed": reconstructed,
    }


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


async def capture_day(
    *,
    user_id: str,
    day: LocalDay,
    local_session_times: list[datetime],
    reconstructed: bool = False,
) -> None:
    """Record one of a learner's days.

    `local_session_times` arrives already loaded, because the backfill reconstructs ninety days
    for one learner and loading their session history once beats loading it ninety times.

    **Consistency is replayed for the day being recorded, not copied from the profile.**
    `LearningProfile.consistencyScore` holds only the present value and is overwritten nightly,
    so copying it onto a row dated last Tuesday would describe this week while claiming to
    describe last Tuesday. Replaying keeps one definition — `consistency_score_from` is the
    stored arithmetic with its window moved — and it means a row written tonight and a row
    reconstructed by the backfill come out of identical code. Two paths would drift, and the
    chart would show a step where they met.
    """
    from ..repository import personal_learning_repo as repo
    from . import reflect_aggregates
    from .behaviour_service import consistency_score_from
    from .reflection_metrics import load_evidence

    evidence = await load_evidence(user_id=user_id, period_start=day.start, period_end=day.end)
    # `reconstructed` decides which mastery question is being asked, because it already means
    # exactly that. Recording the day that just ended asks "what is complete now", and an
    # undated completion certainly happened before a few hours ago — so it counts, and the figure
    # agrees with the dashboard. Reconstructing a day months back asks what was complete *then*,
    # which undated completions cannot answer, so mastery comes back null rather than as a number
    # that silently omits work the learner did.
    mastery = await reflect_aggregates.subject_mastery_on(
        user_id=user_id, as_of=day.end, undated_are_complete=not reconstructed
    )

    await repo.upsert_daily_snapshot(
        user_id=user_id,
        snapshot_date=day.day,
        values=snapshot_values(
            evidence,
            consistency_score=consistency_score_from(local_session_times, as_of=day.end),
            overall_mastery_percent=mastery.overall_percent,
            subject_mastery=mastery.by_course,
            reconstructed=reconstructed,
        ),
    )


async def capture_for_users(user_ids: list[str], *, now: datetime | None = None) -> int:
    """Record each learner's most recently *finished* local day. Returns rows written.

    **The previous local day, not today, and this is the one thing about the schedule worth
    understanding.** The task runs once a night on a single UTC clock, but a snapshot mixes
    per-day activity (minutes, cards, sessions) with state (mastery, consistency), and the
    activity half is only complete once the learner's day is over. A run at 00:30 UTC finds a
    learner in Lagos at 01:30 *on the new day*: recording "today" there would store a day that
    is ninety minutes old and call it finished, and every learner east of UTC would get a row
    of near-zeros. Recording the day that just ended gives every learner exactly one complete
    row per day, at worst a day later than someone far west of UTC might expect.

    Timezones are resolved for the whole batch in one query, because "the previous day" is a
    different date for different learners and resolving per learner would be a query each —
    which is what `resolve_many` exists for.

    One learner failing must not cost the rest of the batch, the same isolation
    `prep_snapshot_service.capture_for_preparations` uses. A failure is logged and skipped: the
    write is idempotent and the backfill can recover the day, so the cost is one row rather
    than the night's work.
    """
    if not user_ids:
        return 0

    from src.shared.time import resolve_many

    from .behaviour_service import BEHAVIOUR_WINDOW_DAYS, load_local_session_times

    reference = now or datetime.now(UTC)
    timezones = await resolve_many(user_ids)

    written = 0
    for user_id in user_ids:
        timezone_ = timezones[user_id]
        try:
            day = local_day_bounds(
                snapshot_day_for(reference, timezone_) - timedelta(days=1), timezone_
            )
            # The consistency window as it stood at the end of that day, which is what
            # `consistency_score_from` needs to replay the stored definition.
            session_times = await load_local_session_times(
                user_id=user_id,
                since=day.end - timedelta(days=BEHAVIOUR_WINDOW_DAYS),
                timezone_=timezone_,
            )
            await capture_day(
                user_id=user_id,
                day=day,
                local_session_times=session_times,
                reconstructed=False,
            )
            written += 1
        except Exception:
            logger.exception("Failed to write daily learning snapshot", extra={"user_id": user_id})
    return written


async def capture_all(*, now: datetime | None = None) -> tuple[int, int]:
    """Record the finished day for every learner with a profile. Returns ``(written, seen)``.

    Batched so memory does not scale with the number of learners, following
    `prep_snapshot_service.capture_all` and the paging shape the other nightly tasks use.

    **Every learner with a `LearningProfile` gets a row, including learners who did nothing.**
    That is deliberate rather than wasteful: `activeDay` would be constantly true and therefore
    carry no information if rows only existed for days with activity, and the rhythm chart's
    question is *which days did they show up* — which a table recording only the days they did
    cannot answer. A day off is a fact about the learner's week and needs somewhere to live.
    """
    from ..repository import personal_learning_repo as repo

    reference = now or datetime.now(UTC)
    written = 0
    seen = 0
    skip = 0

    while True:
        profiles = await repo.list_active_profiles(skip=skip, take=_BATCH_SIZE)
        if not profiles:
            break

        seen += len(profiles)
        written += await capture_for_users([profile.user_id for profile in profiles], now=reference)

        if len(profiles) < _BATCH_SIZE:
            break
        skip += _BATCH_SIZE

    return written, seen


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

# 90 days because that is the longest range the design offers. Reconstructing further would
# store history no chart can ask for.
BACKFILL_DAYS = 90


async def backfill_for_user(
    *, user_id: str, days: int = BACKFILL_DAYS, now: datetime | None = None
) -> int:
    """Reconstruct a learner's recent history. Returns rows written.

    The initial audit assumed this history was unrecoverable. Reading the tables showed the
    opposite: `focusedMinutes`, `sessionsCompleted`, `activeDay`, `cardsReviewed`,
    `recallPercent`, `topicsCompleted` and `effortScore` all reduce to event rows with
    timestamps, and consistency is a pure function of the session set, so every one of those
    recomputes **exactly** for any past day. Only mastery is approximate — see
    `reflect_aggregates.subject_mastery_on` for the two ways it understates — which is what
    `reconstructed` records (Decision P).

    **Days that already have a row are skipped rather than rewritten.** A row written by the
    nightly task measured that day's mastery against the topic count as it stood then, which is
    exact; a reconstruction of the same day measures it against today's, which is not. Letting
    the backfill overwrite would degrade real measurements into estimates and flip their flag to
    say so. It also makes re-running cheap, which matters for a job that can be interrupted.

    **Every read is window-wide, and this is the difference between a job that finishes and one
    that does not.** Reconstructing through `capture_day` — which is the right shape for the nightly
    task's single day — issues about fourteen queries per day, so ninety days is roughly 1,260 round
    trips per learner over the same rows. Measured against this database's ~209 ms round trip that
    was 26 minutes for six learners, and would be on the order of seventy hours at a thousand. It is
    now three reads and one write per learner: the session history, the evidence bucketed by day, the
    mastery series, and a single bulk upsert.
    """
    from src.shared.time import resolve_learner_timezone

    from ..repository import personal_learning_repo as repo
    from . import reflect_aggregates
    from .behaviour_service import (
        BEHAVIOUR_WINDOW_DAYS,
        consistency_score_from,
        load_local_session_times,
    )

    reference = now or datetime.now(UTC)
    timezone_ = await resolve_learner_timezone(user_id)

    # The last `days` *finished* days. Today is excluded because it is still in progress, and
    # storing a partial day as a complete one is the failure the nightly schedule avoids.
    today = snapshot_day_for(reference, timezone_)
    window = [
        local_day_bounds(today - timedelta(days=offset), timezone_)
        for offset in range(max(1, days), 0, -1)
    ]

    existing = {
        snapshot.snapshot_date
        for snapshot in await repo.list_daily_snapshots(
            user_id, since=window[0].day, until=window[-1].day
        )
    }
    missing = [day for day in window if day.day not in existing]
    if not missing:
        return 0

    session_times = await load_local_session_times(
        user_id=user_id,
        since=missing[0].start - timedelta(days=BEHAVIOUR_WINDOW_DAYS),
        timezone_=timezone_,
    )

    # Three window-wide reads and one write, rather than fourteen queries per day. `capture_day` is
    # the right shape for the nightly task, which does one day; calling it ninety times re-reads the
    # same rows ninety times, which measured at 26 minutes for six learners and would be some
    # seventy hours at a thousand.
    from .reflection_metrics import MetricEvidence, load_daily_evidence

    evidence_by_day = await load_daily_evidence(
        user_id=user_id,
        period_start=missing[0].start,
        period_end=missing[-1].end,
        timezone_=timezone_,
    )
    mastery_by_day = await reflect_aggregates.subject_mastery_series(
        user_id=user_id,
        days=[day.day for day in missing],
        undated_are_complete=False,
    )

    rows: list[tuple[date, dict[str, Any]]] = []
    for day in missing:
        mastery = mastery_by_day[day.day]
        rows.append(
            (
                day.day,
                snapshot_values(
                    # A day with no evidence still gets a row: a day off is a fact about the
                    # learner's week, and `activeDay` would carry no information without it.
                    evidence_by_day.get(day.day, MetricEvidence()),
                    consistency_score=consistency_score_from(session_times, as_of=day.end),
                    overall_mastery_percent=mastery.overall_percent,
                    subject_mastery=mastery.by_course,
                    reconstructed=True,
                ),
            )
        )

    try:
        return await repo.bulk_upsert_daily_snapshots(user_id=user_id, rows=rows)
    except Exception:
        # One learner failing must not abandon the run. The write is idempotent and recorded days
        # are skipped, so re-running picks up exactly the gap this left.
        logger.exception(
            "Failed to reconstruct daily learning snapshots", extra={"user_id": user_id}
        )
        return 0


async def backfill_all(
    *, days: int = BACKFILL_DAYS, now: datetime | None = None
) -> tuple[int, int]:
    """Reconstruct history for every learner with a profile. Returns ``(written, seen)``.

    Paged like the nightly writer. Deliberately not a migration: ninety days times every
    learner is a long job that has to be observable, interruptible and re-runnable, and a
    migration is none of those.
    """
    from ..repository import personal_learning_repo as repo

    reference = now or datetime.now(UTC)
    written = 0
    seen = 0
    skip = 0

    while True:
        profiles = await repo.list_active_profiles(skip=skip, take=_BATCH_SIZE)
        if not profiles:
            break

        seen += len(profiles)
        for profile in profiles:
            written += await backfill_for_user(user_id=profile.user_id, days=days, now=reference)

        if len(profiles) < _BATCH_SIZE:
            break
        skip += _BATCH_SIZE

    return written, seen
