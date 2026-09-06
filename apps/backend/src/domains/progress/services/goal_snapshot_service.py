"""Capture one day's progress per goal, so a goal can have a trajectory at all.

`Goal.progress` is mutated in place. Without this, "how has this goal moved" is unanswerable and the
detail page's chart has nothing behind it. `PrepReadinessSnapshot` and `DailyLearningSnapshot` reached
the same conclusion for their own surfaces; this follows them rather than inventing a third shape.

**The previous local day, matching `daily_snapshot_service`.** That service records the last *finished*
day because a learning snapshot mixes per-day activity with state and the activity half needs the day to
be over. A goal snapshot is pure state, so the reasoning is different but the answer is the same:
reading state shortly after a day ends gives that day's **closing** value, which is what a daily point
should mean. Dating it today would give the newest point a meaning that changed with every run — "as of
whenever the task last fired" — and would put this table's x-axis half a day out from every other chart
on the surface.

**The day is the learner's calendar day**, from `to_learner_local`. `PrepReadinessSnapshot` truncates
to a UTC date and its own docstring records that as a bug; repeating it here would put a goal edited at
23:30 in Lagos on the wrong day of the chart.

**Only goals worth a history.** Archived and cancelled goals are skipped: the learner stopped them, and
continuing to write a flat line for a goal nobody is working on is volume without information. Active
and completed goals are recorded — completed ones because the chart should show where the line reached
100 rather than stopping short of it.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update

from src.shared.database import get_session_factory
from src.shared.time import resolve_many, to_learner_local

from ..db_models import Goal, GoalProgressSnapshot
from . import goal_metrics

logger = logging.getLogger(__name__)

#: Rows per page when walking every learner. Matches the other nightly writers so memory does not
#: scale with the number of learners.
_BATCH_SIZE = 100

#: Statuses whose progress is worth a daily row. See the module docstring.
_TRACKED_STATUSES = ("ACTIVE", "COMPLETED")


async def capture_for_users(user_ids: list[str], *, now: datetime | None = None) -> int:
    """Record the last finished local day for every tracked goal of these learners.

    Returns rows written.

    Timezones are resolved for the whole batch in one query, because "today" is a different date for
    different learners and resolving per learner would be a query each — which is what `resolve_many`
    exists for.

    One learner failing must not cost the rest of the batch, the isolation
    `daily_snapshot_service.capture_for_users` and `prep_snapshot_service` both use. A failure is
    logged and skipped; the write is idempotent, so tomorrow's run recovers everything except that
    learner's one missing day.
    """
    if not user_ids:
        return 0

    reference = now or datetime.now(UTC)
    timezones = await resolve_many(user_ids)

    factory = get_session_factory()
    written = 0

    for user_id in user_ids:
        try:
            # The day that just ended in the learner's own calendar. `to_learner_local` first, then
            # step back a day — stepping back in UTC and converting afterwards lands on the wrong
            # date for anyone whose offset crosses midnight.
            captured_on = to_learner_local(reference, timezones[user_id]).date() - timedelta(days=1)

            async with factory() as session:
                goals = (
                    (
                        await session.execute(
                            select(Goal).where(
                                Goal.user_id == user_id,
                                Goal.status.in_(_TRACKED_STATUSES),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

            if not goals:
                continue

            # One query per metric kind present across the whole set, not one per goal — the same
            # batching `_goal_responses` uses, and the reason a learner with twenty goals costs the
            # same as a learner with two.
            measurements = await goal_metrics.derive_current_values(goals, now=reference)

            written += await _upsert_day(
                user_id=user_id,
                captured_on=captured_on,
                goals=goals,
                measurements=measurements,
            )
        except Exception as exc:  # noqa: BLE001 — one learner must not fail the batch
            logger.warning("Goal progress snapshot failed for user %s: %s", user_id, exc)

    return written


async def _upsert_day(
    *,
    user_id: str,
    captured_on: date,
    goals: list,
    measurements: dict,
) -> int:
    """Write or update one day's row per goal, in one session.

    Reads the day's existing rows first and updates in place rather than relying on a database upsert,
    because `updatedAt` should move when a figure is corrected and the row count should not grow. The
    unique index is still what guarantees one row per goal per day under a concurrent run.
    """
    factory = get_session_factory()
    goal_ids = [goal.id for goal in goals]

    async with factory() as session:
        existing_rows = (
            (
                await session.execute(
                    select(GoalProgressSnapshot).where(
                        GoalProgressSnapshot.goal_id.in_(goal_ids),
                        GoalProgressSnapshot.captured_on == captured_on,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = {row.goal_id: row for row in existing_rows}

        #: `{id, progress}` for goals whose stored column has fallen behind, written in one statement
        #: after the loop rather than re-selecting each row.
        drifted: list[dict[str, object]] = []

        for goal in goals:
            measurement = measurements.get(goal.id)
            progress = goal_metrics.derived_progress(goal, measurement)
            # A `manual` goal's value lives on the row; every other kind's is derived. `measured`
            # records which, because `metricKind` can be edited later and a reader could not tell.
            current_value = (
                measurement.current_value if measurement is not None else goal.current_value
            )
            measured = measurement.measured if measurement is not None else False

            row = existing.get(goal.id)
            if row is None:
                session.add(
                    GoalProgressSnapshot(
                        goal_id=goal.id,
                        user_id=user_id,
                        captured_on=captured_on,
                        progress=progress,
                        current_value=current_value,
                        current_value_measured=measured,
                        status=goal.status,
                    )
                )
            else:
                row.progress = progress
                row.current_value = current_value
                row.current_value_measured = measured
                row.status = goal.status

            # Bring the stored column to the derived figure.
            #
            # Every reader that matters now derives progress, so this is not what makes the API
            # correct. It is here so the column stops being a lie: `Goal.progress` is selected
            # directly by anything not yet routed through `derived_progress`, and it is what an
            # export, a migration or a hand-written query will read. `update_progress` was its only
            # writer and has never been called from anywhere in `src`.
            #
            # Only for measured kinds, and only on a real change. A `manual` goal's figure is the
            # learner's own and is never touched, and writing an unchanged value would move
            # `updatedAt` on every goal every night.
            if (
                (goal.metric_kind or "manual") != "manual"
                and measurement is not None
                and measurement.current_value is not None
                and abs(float(goal.progress or 0.0) - progress) > 0.05
            ):
                drifted.append({"id": goal.id, "progress": progress})

        if drifted:
            # One executemany against the primary key, not a select-then-assign per goal.
            await session.execute(update(Goal), drifted)

        await session.commit()

    return len(goals)


async def capture_all(*, now: datetime | None = None) -> tuple[int, int]:
    """Record the finished day for every learner who has a goal.

    Returns ``(rows_written, learners_seen)``.

    Paged over the learners who actually hold goals rather than over every profile, which is the
    difference from `daily_snapshot_service.capture_all`: that table needs a row for every learner
    every day because "did they show up" is only answerable from a complete series, while a learner
    with no goals has no trajectory to record and an empty row would say nothing.
    """
    reference = now or datetime.now(UTC)
    factory = get_session_factory()

    written = 0
    seen = 0
    skip = 0

    while True:
        async with factory() as session:
            user_ids = list(
                (
                    await session.execute(
                        select(Goal.user_id)
                        .where(Goal.status.in_(_TRACKED_STATUSES))
                        .group_by(Goal.user_id)
                        .order_by(Goal.user_id)
                        .offset(skip)
                        .limit(_BATCH_SIZE)
                    )
                )
                .scalars()
                .all()
            )

        if not user_ids:
            break

        seen += len(user_ids)
        written += await capture_for_users(user_ids, now=reference)

        if len(user_ids) < _BATCH_SIZE:
            break
        skip += _BATCH_SIZE

    return written, seen


async def list_history(
    *, user_id: str, goal_id: str, since: date, until: date
) -> list[GoalProgressSnapshot]:
    """One goal's rows across a window, oldest first.

    Scoped by `userId` as well as `goalId`. The goal is already the learner's by the time a route
    calls this, but a history read that trusts only the path parameter is one refactor away from
    serving another learner's series.
    """
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(GoalProgressSnapshot)
                    .where(
                        GoalProgressSnapshot.goal_id == goal_id,
                        GoalProgressSnapshot.user_id == user_id,
                        GoalProgressSnapshot.captured_on >= since,
                        GoalProgressSnapshot.captured_on <= until,
                    )
                    .order_by(GoalProgressSnapshot.captured_on.asc())
                )
            )
            .scalars()
            .all()
        )


async def first_captured_on(*, user_id: str, goal_id: str) -> date | None:
    """The earliest day recorded for this goal, or `None` if none is.

    Published so the client can say "building since Tuesday" rather than showing an empty chart with
    no explanation. `None` is the honest answer for a goal whose first nightly run has not happened —
    the distinction Decision Y turns on, and the reason the trend response carries it rather than
    letting an empty `points` array stand for two different situations.
    """
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(GoalProgressSnapshot.captured_on)
                .where(
                    GoalProgressSnapshot.goal_id == goal_id,
                    GoalProgressSnapshot.user_id == user_id,
                )
                .order_by(GoalProgressSnapshot.captured_on.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
