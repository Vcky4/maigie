"""
Course generation tasks (Celery).

These tasks run in a separate worker process and communicate progress/results
back to the API server through Redis pubsub, which then forwards to chat
WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.database import db
from src.services.llm_service import llm_service
from src.services.ws_event_bus import publish_ws_event
from src.tasks.base import task
from src.tasks.registry import register_task

logger = logging.getLogger(__name__)

TASK_NAME = "course.generate_from_chat"


async def _ensure_db_connected() -> None:
    if not db.is_connected():
        await db.connect()


async def _delete_existing_course_content(course_id: str) -> None:
    # Delete topics first, then modules (to satisfy FK constraints).
    modules = await db.module.find_many(where={"courseId": course_id})
    module_ids = [m.id for m in modules]
    if module_ids:
        await db.topic.delete_many(where={"moduleId": {"in": module_ids}})
    await db.module.delete_many(where={"courseId": course_id})


async def _persist_course_outline(course_id: str, outline: dict[str, Any]) -> None:
    modules = outline.get("modules") or []

    for i, mod in enumerate(modules):
        module_title = (mod.get("title") or f"Module {i+1}").strip()
        topics = mod.get("topics") or []

        module = await db.module.create(
            data={
                "courseId": course_id,
                "title": module_title,
                "order": float(i),
                "description": mod.get("description") or None,
            }
        )

        for j, topic in enumerate(topics):
            if isinstance(topic, str):
                topic_title = topic.strip()
            else:
                topic_title = str(topic.get("title") or f"Topic {j+1}").strip()

            if not topic_title:
                continue

            await db.topic.create(
                data={
                    "moduleId": module.id,
                    "title": topic_title,
                    "order": float(j),
                }
            )


@register_task(
    name=TASK_NAME,
    description="Generate a course outline from a chat request and persist it",
    category="courses",
    tags=["courses", "ai", "generation"],
)
@task(name=TASK_NAME, bind=True, max_retries=3)
def generate_course_from_chat_task(  # type: ignore[misc]
    self: Any,
    *,
    user_id: str,
    course_id: str,
    user_message: str,
    topic: str,
    difficulty: str,
) -> dict[str, Any]:
    """
    Generate a course outline in the background and store it in the DB.

    Notes:
    - This is a synchronous Celery task wrapper that runs async logic using asyncio.run().
    - Emits websocket events via Redis pubsub so the API server can forward updates.
    """

    async def _run() -> dict[str, Any]:
        await _ensure_db_connected()

        # Started (optional; frontend currently reacts mainly to success)
        await publish_ws_event(
            user_id,
            {
                "status": "processing",
                "action": "ai_course_generation",
                "course_id": course_id,
                "courseId": course_id,
                "message": "Generating your course outline...",
            },
        )

        outline = await llm_service.generate_course_outline(
            topic=topic,
            difficulty=difficulty,
            user_message=user_message,
        )

        await _delete_existing_course_content(course_id)
        await _persist_course_outline(course_id, outline)

        # Update course description if provided
        description = outline.get("description") or f"A course about {topic}."
        await db.course.update(
            where={"id": course_id},
            data={
                "description": description,
                "isAIGenerated": True,
                "progress": 0.0,
            },
        )

        await publish_ws_event(
            user_id,
            {
                "status": "success",
                "action": "create_course",
                "course_id": course_id,
                "courseId": course_id,
                "message": "Your course is ready!",
            },
        )

        return {"status": "success", "course_id": course_id}

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(f"Course generation task failed: {e}", exc_info=True)

        # Best-effort notify user
        try:
            asyncio.run(
                publish_ws_event(
                    user_id,
                    {
                        "status": "error",
                        "action": "ai_course_generation",
                        "course_id": course_id,
                        "courseId": course_id,
                        "message": "Course generation failed. Please try again.",
                    },
                )
            )
        except Exception:
            pass

        raise
