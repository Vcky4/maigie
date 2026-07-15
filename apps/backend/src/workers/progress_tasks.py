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

        from src.shared.database import db

        yesterday = datetime.now(UTC) - timedelta(days=1)
        # Find streaks where lastStudyDate is older than yesterday
        # and currentStreak > 0 — those are broken
        broken = await db.userstreak.find_many(
            where={
                "currentStreak": {"gt": 0},
                "lastStudyDate": {"lt": yesterday},
            }
        )
        for streak in broken:
            await db.userstreak.update(
                where={"userId": streak.userId},
                data={"currentStreak": 0},
            )
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
        from src.shared.database import db

        result = await db.execute_raw(
            'UPDATE "User" SET "creditsUsedToday" = 0 WHERE "creditsUsedToday" > 0 AND role = \'USER\''
        )
        logger.info("Daily credit reset complete")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_reset())
    finally:
        loop.close()
