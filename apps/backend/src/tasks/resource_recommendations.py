"""
Resource recommendation tasks (Celery).

Runs expensive RAG + DB writes in the worker and reports progress/results to the UI
via Redis pubsub -> API websocket forwarder.
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

TASK_NAME = "resources.recommend_from_chat"


async def _ensure_db_connected() -> None:
    if not db.is_connected():
        await db.connect()


@register_task(
    name=TASK_NAME,
    description="Generate resource recommendations and persist them",
    category="resources",
    tags=["resources", "ai", "rag"],
)
@task(name=TASK_NAME, bind=True, max_retries=3)
def recommend_resources_from_chat_task(  # type: ignore[misc]
    self: Any,
    *,
    user_id: str,
    query: str,
    topic_id: str | None = None,
    course_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        await _ensure_db_connected()

        await publish_ws_event(
            user_id,
            {
                "status": "processing",
                "action": "ai_resource_recommendations",
                "progress": 10,
                "stage": "starting",
                "message": "Finding relevant resources...",
            },
        )

        result = await action_service.recommend_resources(
            {"query": query, "topicId": topic_id, "courseId": course_id, "limit": limit},
            user_id,
        )

        if result.get("status") != "success":
            await publish_ws_event(
                user_id,
                {
                    "status": "error",
                    "action": "ai_resource_recommendations",
                    "message": result.get("message", "Failed to recommend resources"),
                },
            )
            return result

        await publish_ws_event(
            user_id,
            {
                "status": "success",
                "action": "recommend_resources",
                "resources": result.get("resources", []),
                "count": result.get("count", 0),
                "message": result.get("message", "Successfully generated resource recommendations"),
            },
        )

        return result

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(f"Resource recommendation task failed: {e}", exc_info=True)
        try:
            asyncio.run(
                publish_ws_event(
                    user_id,
                    {
                        "status": "error",
                        "action": "ai_resource_recommendations",
                        "message": "Resource recommendation failed. Please try again.",
                    },
                )
            )
        except Exception:
            pass
        raise
