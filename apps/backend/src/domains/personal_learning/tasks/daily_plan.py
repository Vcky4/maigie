"""Celery task: Prepare personalized daily plan for each active learner.

Schedule: Daily at 06:00 UTC | Queue: heavy | Max retries: 3
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


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
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_prepare_daily_plan_async())
    finally:
        loop.close()


async def _prepare_daily_plan_async():
    from src.domains.personal_learning.services import (
        notification_service,
        flashcard_service,
        study_plan_service,
    )
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    logger.info("Daily plan task started")

    # Fetch all active learning profiles
    profiles = await repo.list_active_profiles()

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
        except Exception:
            logger.exception(f"Failed to prepare daily plan for user {user_id}")

    logger.info(f"Daily plan task completed for {len(profiles)} learner(s)")
