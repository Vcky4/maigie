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

    from src.tasks.spaced_repetition import process_due_reviews

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
