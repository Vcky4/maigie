"""
Intelligence domain background tasks.

AI course generation, schedule generation, and resource recommendations.
These are CPU/LLM-intensive tasks routed to the 'heavy' queue.
"""

import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="intelligence.generate_course", queue="heavy", time_limit=180)
def generate_course_task(course_id: str, user_id: str, topic_prompt: str, difficulty: str):
    """Generate AI course content (modules + topics) in background.

    Delegates to the existing course generation pipeline.
    """
    import asyncio

    from src.domains.knowledge.services.ai_course_generation import generate_course_content_task

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            generate_course_content_task(
                course_id=course_id,
                user_id=user_id,
                topic_prompt=topic_prompt,
                difficulty=difficulty,
            )
        )
    finally:
        loop.close()


@celery_app.task(name="intelligence.generate_schedule", queue="heavy", time_limit=120)
def generate_schedule_task(user_id: str, preferences: dict | None = None):
    """Generate AI study schedule in background."""
    import asyncio

    from src.domains.intelligence.planning.schedule_regen_impl import regenerate_schedule

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(regenerate_schedule(user_id=user_id, preferences=preferences or {}))
    finally:
        loop.close()


@celery_app.task(name="intelligence.recommend_resources", queue="heavy", time_limit=60)
def recommend_resources_task(user_id: str, query: str, limit: int = 5):
    """Generate resource recommendations in background."""
    import asyncio

    from src.domains.knowledge.services.resource_service import recommend_resources

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(recommend_resources(user_id=user_id, query=query, limit=limit))
    finally:
        loop.close()
