"""Progress domain — what it does in response to other domains' events.

One handler, and it closes a gap that has been described in a comment for as long as the comment has
existed. `knowledge.course_service.toggle_topic_completion` reads:

    if completed:
        await emit_topic_completed(user_id, topic_id, course_id)
        # Spaced repetition: Progress domain listens to topic.completed event
        # and creates ReviewSchedule automatically via the event bus

Nothing listened. `spaced_repetition_impl.ensure_review_item_for_completed_topic` was written for this
exact purpose — its docstring says "Call this after marking a topic complete" — and had **zero callers
anywhere in `src`**. A complete implementation waiting for a caller that was never written, because the
caller was meant to be an event listener and nothing registered any.

So completing a topic has never scheduled a review. That is most of why the review tables are nearly
empty: 20 `ReviewItem` rows across the whole database, none of them due.
"""

from __future__ import annotations

import logging

from src.shared.events import listen

logger = logging.getLogger(__name__)


@listen("topic.completed")
async def schedule_first_review(data: dict) -> None:
    """Open a spaced-repetition schedule for a topic the learner has just finished.

    **Deliberately `create_review_item_for_topic`, not
    `ensure_review_item_for_completed_topic`.** The latter also writes a `ScheduleBlock` for the review,
    and that materialising step is superseded: `agenda_service` composes due reviews on read, so a block
    would be a second record of one commitment — one that has to be found and rewritten every time the
    SM-2 interval moves the due date. The `ReviewItem` alone is what the agenda reads.

    Already idempotent: `create_review_item_for_topic` returns `None` when the topic has a review
    schedule, so a learner reopening and re-completing a topic does not restart their interval or
    create a duplicate.

    Failures are contained here rather than left to the bus. `_safe_dispatch` would log this anyway, but
    a review that could not be scheduled should name itself in the logs — "no review for a completed
    topic" is not something to infer later from an empty table.
    """
    user_id = data.get("user_id")
    topic_id = data.get("topic_id")
    if not user_id or not topic_id:
        return

    from src.domains.progress.services.spaced_repetition_impl import create_review_item_for_topic

    try:
        review = await create_review_item_for_topic(user_id, topic_id)
    except Exception:
        logger.exception(
            "Could not open a review schedule for a completed topic",
            extra={"user_id": user_id, "topic_id": topic_id},
        )
        return

    if review is not None:
        logger.info(
            "Review scheduled for completed topic",
            extra={"user_id": user_id, "topic_id": topic_id, "next_review_at": review.next_review_at},
        )
