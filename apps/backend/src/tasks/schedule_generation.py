"""
Schedule generation/execution tasks (Celery).

Executes schedule creation in the worker (including Google Calendar conflict checks/sync)
and reports progress/results back to the UI via Redis pubsub -> API websocket forwarder.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task

logger = logging.getLogger(__name__)

TASK_NAME = "schedule.create_from_chat"


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


@register_task(
    name=TASK_NAME,
    description="Create one or more schedule blocks from chat action data",
    category="schedule",
    tags=["schedule", "ai"],
)
@task(name=TASK_NAME, bind=True, max_retries=3)
def create_schedule_from_chat_task(  # type: ignore[misc]
    self: Any,
    *,
    user_id: str,
    schedule_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        from src.core.database import db
        from src.services.action_service import action_service
        from src.services.ws_event_bus import publish_ws_event

        await _ensure_db_connected()

        # Idempotency: use the Celery task ID as a deduplication key.
        # If this exact task was already processed (redelivery), skip.
        task_id = self.request.id
        if task_id:
            from src.core.cache import cache

            dedup_key = cache.make_key(["task_dedup", "schedule", task_id])
            already_done = await cache.get(dedup_key)
            if already_done:
                logger.info("Schedule task %s already completed — skipping redelivery", task_id)
                return {"status": "success", "results": [], "idempotent": True}

        total = max(1, len(schedule_blocks))

        await publish_ws_event(
            user_id,
            {
                "status": "processing",
                "action": "ai_schedule_generation",
                "progress": 5,
                "stage": "starting",
                "message": f"Creating {len(schedule_blocks)} schedule block(s)...",
            },
        )

        results: list[dict[str, Any]] = []
        for idx, block in enumerate(schedule_blocks):
            await publish_ws_event(
                user_id,
                {
                    "status": "processing",
                    "action": "ai_schedule_generation",
                    "progress": 5 + int(((idx) / total) * 80),
                    "stage": "creating_blocks",
                    "message": f"Creating block {idx + 1} of {total}...",
                },
            )
            results.append(await action_service.create_schedule(block, user_id))

        # If any failed, report error but still return all results
        any_error = any(r.get("status") != "success" for r in results)

        if any_error:
            await publish_ws_event(
                user_id,
                {
                    "status": "error",
                    "action": "ai_schedule_generation",
                    "message": "Some schedule blocks failed to create.",
                    "results": results,
                },
            )
            return {"status": "error", "results": results}

        # Publish a success event so SchedulePage refetches (it listens for create_schedule success)
        await publish_ws_event(
            user_id,
            {
                "status": "success",
                "action": "create_schedule",
                "message": f"Created {len(results)} schedule block(s).",
                "results": results,
            },
        )

        # Mark task as completed for idempotency (TTL: 1 hour)
        if task_id:
            await cache.set(dedup_key, "done", expire=3600)

        return {"status": "success", "results": results}

    try:
        return run_async_in_celery(_run())
    except Exception as e:
        logger.error(f"Schedule creation task failed: {e}", exc_info=True)
        try:
            from src.services.ws_event_bus import publish_ws_event

            async def _notify_error() -> None:
                await publish_ws_event(
                    user_id,
                    {
                        "status": "error",
                        "action": "ai_schedule_generation",
                        "message": "Schedule creation failed. Please try again.",
                    },
                )

            run_async_in_celery(_notify_error())
        except Exception:
            pass
        raise
