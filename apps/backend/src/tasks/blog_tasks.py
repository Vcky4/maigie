"""
Blog Autopilot Celery Tasks.

Runs the blog content generation pipeline on a schedule.
Checks the content calendar daily and processes due entries.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task
from src.tasks.schedules import register_periodic_task

logger = logging.getLogger(__name__)

TASK_BLOG_AUTOPILOT = "blog.autopilot_run"


async def _ensure_db_connected():
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _blog_autopilot_impl() -> dict:
    """Run the blog autopilot pipeline."""
    await _ensure_db_connected()
    from src.services.blog_autopilot_service import run_blog_autopilot

    return await run_blog_autopilot()


@register_task(
    name=TASK_BLOG_AUTOPILOT,
    description="Check content calendar and generate/publish scheduled blog posts",
    category="blog",
    tags=["blog", "content", "autopilot", "ai"],
)
@task(name=TASK_BLOG_AUTOPILOT, bind=True, max_retries=1)
def blog_autopilot_task(self: Any) -> dict:
    """Run blog autopilot."""
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping blog autopilot (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}
    return run_async_in_celery(_blog_autopilot_impl())


def register_blog_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for blog autopilot."""
    from celery.schedules import crontab

    # Run daily at 7 AM UTC — generates and publishes scheduled posts
    register_periodic_task(
        name="blog.autopilot.daily",
        schedule=crontab(minute=0, hour=7),
        task=TASK_BLOG_AUTOPILOT,
    )

    logger.info("Registered blog beat tasks")


register_blog_beat_tasks()
