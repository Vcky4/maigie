"""
Schedule generation/execution tasks (Celery).

Executes schedule creation in the worker (including Google Calendar conflict checks/sync)
and reports progress/results back to the UI via Redis pubsub -> API websocket forwarder.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.database import db
from src.services.action_service import action_service
from src.services.ws_event_bus import publish_ws_event
from src.tasks.base import task
from src.tasks.registry import register_task

logger = logging.getLogger(__name__)

TASK_NAME = "schedule.create_from_chat"


async def _ensure_db_connected() -> None:
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
        await _ensure_db_connected()

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

        return {"status": "success", "results": results}

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(f"Schedule creation task failed: {e}", exc_info=True)
        try:
            asyncio.run(
                publish_ws_event(
                    user_id,
                    {
                        "status": "error",
                        "action": "ai_schedule_generation",
                        "message": "Schedule creation failed. Please try again.",
                    },
                )
            )
        except Exception:
            pass
        raise
