"""Celery task: Prepare personalized daily plan for each active learner.

Schedule: Daily at 06:00 UTC | Queue: heavy | Max retries: 3

Uses paginated batch processing to avoid loading all users into memory.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50  # Process users in batches of 50


@celery_app.task(
    name="learning.prepare_daily_plan",
    queue="heavy",
    max_retries=3,
    time_limit=300,
    soft_time_limit=240,
)
def prepare_daily_plan():
    """
    For each active learner, compose a daily plan from schedule blocks,
    due reviews, and study plan items. Create a notification with the plan
    respecting the learner's quiet hours.

    Processes users in paginated batches to limit memory and DB pressure.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_prepare_daily_plan_async())
    finally:
        loop.close()


async def _prepare_daily_plan_async():
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import (
        notification_service,
    )

    logger.info("Daily plan task started")

    total_processed = 0
    skip = 0

    while True:
        # Fetch one batch
        profiles = await repo.list_active_profiles(skip=skip, take=_BATCH_SIZE)
        if not profiles:
            break

        for profile in profiles:
            user_id = profile.user_id
            try:
                # Gather due flashcards and active plan items for today
                due_flashcards = await repo.list_due_flashcards(user_id, limit=10)
                active_plans = await repo.list_active_plans(user_id)

                # Build plan summary
                plan_parts = []
                if due_flashcards:
                    plan_parts.append(f"{len(due_flashcards)} flashcard(s) due for review")
                if active_plans:
                    plan_parts.append(f"{len(active_plans)} active study plan(s)")

                if plan_parts:
                    body = "Today's focus: " + "; ".join(plan_parts)
                else:
                    body = "No pending reviews today. Great time to explore something new!"

                await notification_service.create_notification(
                    user_id=user_id,
                    type="DAILY_PLAN",
                    title="Your Daily Learning Plan",
                    body=body,
                    priority=3,
                    action_data={"type": "navigate", "target": "/home"},
                )
                total_processed += 1
            except Exception:
                logger.exception(f"Failed to prepare daily plan for user {user_id}")

        skip += _BATCH_SIZE

        # If we got fewer than batch size, we're done
        if len(profiles) < _BATCH_SIZE:
            break

    logger.info(f"Daily plan task completed for {total_processed} learner(s)")
