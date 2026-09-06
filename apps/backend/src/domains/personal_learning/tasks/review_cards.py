"""Review cards generated from completed study-plan items.

Backs the create wizard's "Generate review cards" option. Queued from the item
completion path rather than awaited there, because generation is an LLM round trip and a
learner ticking a task off should not wait on it — nor have the completion fail if the
model does.

Queue: default (a single short generation, not a batch)
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.generate_plan_item_cards",
    queue="default",
    max_retries=2,
    time_limit=180,
    soft_time_limit=150,
)
def generate_plan_item_cards_task(user_id: str, plan_id: str, item_id: str):
    """Generate review cards for one completed plan item."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run(user_id, plan_id, item_id))
    finally:
        loop.close()


async def _run(user_id: str, plan_id: str, item_id: str) -> int:
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.services import study_plan_service

    try:
        cards = await study_plan_service.generate_review_cards_for_item(
            user_id=user_id, plan_id=plan_id, item_id=item_id
        )
    except Exception:
        # Logged and swallowed. The learner's completion already succeeded, and retrying
        # a model that just refused this prompt is unlikely to help; the next completed
        # item will try again.
        logger.exception(
            "Review-card generation failed",
            extra={"plan_id": plan_id, "item_id": item_id},
        )
        return 0

    logger.info(
        "Generated %d review card(s) from plan item",
        len(cards),
        extra={"plan_id": plan_id, "item_id": item_id},
    )
    return len(cards)
