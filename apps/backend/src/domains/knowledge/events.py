"""
Knowledge domain — Domain events.

Events emitted when courses, topics, or resources change.
Progress and Intelligence domains listen to these.
"""

from src.shared.events import KnowledgeEvents, emit


async def emit_course_created(
    user_id: str, course_id: str, *, is_ai_generated: bool = False
) -> None:
    await emit(
        KnowledgeEvents.COURSE_CREATED,
        {
            "user_id": user_id,
            "course_id": course_id,
            "is_ai_generated": is_ai_generated,
        },
    )


async def emit_course_completed(user_id: str, course_id: str) -> None:
    await emit(KnowledgeEvents.COURSE_COMPLETED, {"user_id": user_id, "course_id": course_id})


async def emit_topic_completed(user_id: str, topic_id: str, course_id: str) -> None:
    """Emitted when a learner marks a topic complete."""
    await emit(
        KnowledgeEvents.TOPIC_COMPLETED,
        {
            "user_id": user_id,
            "topic_id": topic_id,
            "course_id": course_id,
        },
    )


async def emit_topic_uncompleted(user_id: str, topic_id: str, course_id: str) -> None:
    """Emitted when a learner unmarks a topic."""
    await emit(
        KnowledgeEvents.TOPIC_UNCOMPLETED,
        {
            "user_id": user_id,
            "topic_id": topic_id,
            "course_id": course_id,
        },
    )


async def emit_resource_added(user_id: str, resource_id: str, course_id: str | None = None) -> None:
    await emit(
        KnowledgeEvents.RESOURCE_ADDED,
        {
            "user_id": user_id,
            "resource_id": resource_id,
            "course_id": course_id,
        },
    )
