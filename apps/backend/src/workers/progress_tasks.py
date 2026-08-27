"""
Progress domain background tasks.

Spaced repetition scheduling, streak maintenance, and achievement checks.
Routed to 'default' queue (lightweight, frequent).
"""

import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="progress.process_spaced_repetition", queue="default", time_limit=60)
def process_spaced_repetition_task():
    """Process due spaced repetition reviews and create schedule blocks."""
    import asyncio

    from src.domains.progress.services.spaced_repetition_impl import process_due_reviews

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(process_due_reviews())
    finally:
        loop.close()


@celery_app.task(name="progress.check_streaks", queue="default", time_limit=30)
def check_streaks_task():
    """Reset broken streaks (run daily at midnight UTC)."""
    import asyncio

    async def _reset_broken_streaks():
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select, update

        from src.domains.progress.db_models import UserStreak
        from src.shared.database import get_session_factory

        yesterday = datetime.now(UTC) - timedelta(days=1)
        factory = get_session_factory()
        async with factory() as session:
            # Find streaks where lastStudyDate is older than yesterday
            # and currentStreak > 0 — those are broken
            stmt = select(UserStreak).where(
                UserStreak.current_streak > 0,
                UserStreak.last_study_date < yesterday,
            )
            result = await session.execute(stmt)
            broken = list(result.scalars().all())

            for streak in broken:
                upd = (
                    update(UserStreak)
                    .where(UserStreak.user_id == streak.user_id)
                    .values(current_streak=0)
                )
                await session.execute(upd)

            await session.commit()

        if broken:
            logger.info(f"Reset {len(broken)} broken streaks")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_reset_broken_streaks())
    finally:
        loop.close()


@celery_app.task(name="progress.daily_credit_reset", queue="default", time_limit=30)
def daily_credit_reset_task():
    """Reset daily credit counters for free tier users."""
    import asyncio

    async def _reset():
        from sqlalchemy import text

        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    'UPDATE "User" SET "creditsUsedToday" = 0 WHERE "creditsUsedToday" > 0 AND role = \'USER\''
                )
            )
            await session.commit()
        logger.info("Daily credit reset complete")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_reset())
    finally:
        loop.close()


@celery_app.task(
    name="progress.capture_goal_progress",
    queue="heavy",
    max_retries=3,
    time_limit=1800,
    soft_time_limit=1740,
)
def capture_goal_progress_task():
    """Write one progress row per tracked goal, for each learner's current local day.

    `heavy`, not `default`, because this walks every learner holding a goal and derives each one's
    measured value — the same shape as `learning.capture_daily_snapshots` rather than the streak reset
    above it.

    **Records each learner's last finished local day**, the same date convention as
    `learning.capture_daily_snapshots`, so both tables share an x-axis. Progress is pure state, so
    reading it shortly after a day ends gives that day's closing value. The writer is idempotent on
    `(goalId, capturedOn)`, so a retry or an overlapping run corrects the row rather than duplicating
    the day.
    """
    import asyncio

    async def _capture():
        from src.domains.progress.services import goal_snapshot_service
        from src.shared.database.session import ensure_db

        await ensure_db()
        logger.info("Goal progress snapshot task started")
        written, seen = await goal_snapshot_service.capture_all()
        logger.info("Goal progress rows written: %s across %s learner(s)", written, seen)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_capture())
    finally:
        loop.close()


@celery_app.task(
    name="progress.review_goal_lifecycle",
    queue="default",
    max_retries=2,
    time_limit=900,
    soft_time_limit=840,
)
def review_goal_lifecycle_task():
    """Take at most one action on each goal whose deadline is near or already passed.

    **Nothing used to look.** `is_at_risk`, `is_due_soon` and `is_overdue` were written, pure, and read
    only when a learner opened a page — so a goal went overdue and the only thing that noticed was a
    label on a screen the learner had stopped visiting.

    `default` rather than `heavy`: the candidate set is bounded in SQL to goals with a deadline inside the
    due-soon horizon, which is a small slice of the goal table rather than a walk over all of it.

    Runs after `progress.capture_goal_progress` on purpose. Extensions are sized from the rate recorded in
    `GoalProgressSnapshot`, so reviewing before the night's snapshot was written would size today's
    decision from a rate a day out of date.
    """
    import asyncio

    async def _review():
        from src.domains.progress.services import goal_lifecycle_service
        from src.shared.database.session import ensure_db

        await ensure_db()
        counts = await goal_lifecycle_service.review_goals()
        logger.info("Goal lifecycle actions taken: %s", counts or "none")
        return counts

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_review())
    finally:
        loop.close()


def get_beat_schedule() -> dict:
    """Beat entries for the progress domain.

    Only the goal snapshot is listed. `progress.process_spaced_repetition`, `progress.check_streaks`
    and `progress.daily_credit_reset` are registered above but have never had a schedule — that is a
    pre-existing gap, and scheduling a sweep that has not been running is a behaviour change rather
    than wiring, so it is left alone and recorded here instead of quietly switched on.
    """
    from celery.schedules import crontab

    return {
        # 01:30 UTC, fifteen minutes after `learning.capture_daily_snapshots`. Deliberately not the
        # same minute: both walk every learner and open their own connections, and the pooler has
        # already been exhausted once in this programme by eight concurrent reads. Which day each
        # learner gets is decided from their own timezone, not from this clock.
        "progress.capture_goal_progress": {
            "task": "progress.capture_goal_progress",
            "schedule": crontab(hour=1, minute=30),
            "options": {"queue": "heavy"},
        },
        # 02:30, an hour after the snapshot above. That order is load-bearing: an extension is sized from
        # the rate recorded in `GoalProgressSnapshot`, so reviewing first would size tonight's decision
        # from a rate that is a day stale. Also clear of 02:00 (`learning.analyze_behaviour`), because
        # both walk learners and open their own connections, and the pooler has been exhausted once
        # already in this programme by concurrent reads.
        "progress.review_goal_lifecycle": {
            "task": "progress.review_goal_lifecycle",
            "schedule": crontab(hour=2, minute=30),
            "options": {"queue": "default"},
        },
    }
