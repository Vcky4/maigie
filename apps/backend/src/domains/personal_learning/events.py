"""
Personal Learning domain — Domain events.

Events emitted by personal learning activities.
Events consumed from other domains (progress, classrooms, knowledge).
"""

import logging

from src.shared.events import (
    ClassroomEvents,
    KnowledgeEvents,
    PersonalLearningEvents,
    ProgressEvents,
    emit,
    listen,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Events Emitted
# ===========================================================================


async def emit_note_created(
    user_id: str,
    note_id: str,
    *,
    title: str,
    course_id: str | None = None,
    topic_id: str | None = None,
) -> None:
    await emit(
        PersonalLearningEvents.NOTE_CREATED,
        {
            "user_id": user_id,
            "note_id": note_id,
            "title": title,
            "course_id": course_id,
            "topic_id": topic_id,
        },
    )


async def emit_topic_studied(
    user_id: str, topic_id: str, course_id: str, duration_seconds: int
) -> None:
    await emit(
        PersonalLearningEvents.TOPIC_STUDIED,
        {
            "user_id": user_id,
            "topic_id": topic_id,
            "course_id": course_id,
            "duration_seconds": duration_seconds,
        },
    )


async def emit_topic_completed(user_id: str, topic_id: str, course_id: str) -> None:
    await emit(
        PersonalLearningEvents.TOPIC_COMPLETED,
        {
            "user_id": user_id,
            "topic_id": topic_id,
            "course_id": course_id,
        },
    )


async def emit_quiz_completed(
    user_id: str, prep_id: str, quiz_id: str, score: float, weak_topics: list[str]
) -> None:
    await emit(
        PersonalLearningEvents.QUIZ_COMPLETED,
        {
            "user_id": user_id,
            "prep_id": prep_id,
            "quiz_id": quiz_id,
            "score": score,
            "weak_topics": weak_topics,
        },
    )


async def emit_study_session_ended(
    user_id: str, session_id: str, duration: float, context: dict | None = None
) -> None:
    await emit(
        PersonalLearningEvents.STUDY_SESSION_ENDED,
        {
            "user_id": user_id,
            "session_id": session_id,
            "duration": duration,
            "context": context,
        },
    )


async def emit_flashcard_reviewed(
    user_id: str, card_id: str, quality: int, deck_id: str | None = None
) -> None:
    await emit(
        PersonalLearningEvents.FLASHCARD_REVIEWED,
        {
            "user_id": user_id,
            "card_id": card_id,
            "quality": quality,
            "deck_id": deck_id,
        },
    )


async def emit_preparation_completed(user_id: str, prep_id: str, subject: str) -> None:
    await emit(
        PersonalLearningEvents.PREPARATION_COMPLETED,
        {
            "user_id": user_id,
            "prep_id": prep_id,
            "subject": subject,
        },
    )


async def emit_milestone_reached(
    user_id: str, milestone_type: str, milestone_value: int | str
) -> None:
    await emit(
        PersonalLearningEvents.MILESTONE_REACHED,
        {
            "user_id": user_id,
            "milestone_type": milestone_type,
            "milestone_value": milestone_value,
        },
    )


async def emit_study_plan_item_completed(user_id: str, plan_id: str, item_id: str) -> None:
    await emit(
        PersonalLearningEvents.STUDY_PLAN_ITEM_COMPLETED,
        {
            "user_id": user_id,
            "plan_id": plan_id,
            "item_id": item_id,
        },
    )


# ===========================================================================
# Events Consumed (from other domains)
# ===========================================================================


@listen(ProgressEvents.STREAK_UPDATED)
async def handle_streak_updated(data: dict) -> None:
    """Check for streak milestones and generate celebration notifications."""
    from .services import notification_service

    user_id = data.get("user_id")
    streak_count = data.get("streak_count", 0)

    if not user_id:
        return

    milestone_streaks = [7, 14, 30, 60, 100, 365]
    if streak_count in milestone_streaks:
        await notification_service.create_notification(
            user_id=user_id,
            type="celebration",
            title=f"\U0001f525 {streak_count}-day streak!",
            body=f"You've studied for {streak_count} days straight. Incredible consistency!",
            priority=2,
        )
        await emit_milestone_reached(user_id, "streak", streak_count)


@listen(ProgressEvents.ACHIEVEMENT_UNLOCKED)
async def handle_achievement_unlocked(data: dict) -> None:
    """Surface achievement in activity feed and create notification."""
    from .services import notification_service

    user_id = data.get("user_id")
    title = data.get("title", "Achievement Unlocked")

    if not user_id:
        return

    await notification_service.create_notification(
        user_id=user_id,
        type="celebration",
        title=f"\U0001f3c6 {title}",
        body="You've unlocked a new achievement!",
        priority=3,
    )


@listen(ClassroomEvents.SESSION_ENDED)
async def handle_classroom_session(data: dict) -> None:
    """Surface classroom connections in personal learning home."""
    # Check if the classroom topic matches any personal learning topics
    # This creates connection entries for the home service to surface
    logger.debug(f"Classroom session ended: {data}")


@listen(ClassroomEvents.DISCUSSION_CREATED)
async def handle_classroom_discussion(data: dict) -> None:
    """Surface relevant classroom discussions."""
    logger.debug(f"Classroom discussion created: {data}")


@listen(KnowledgeEvents.TOPIC_COMPLETED)
async def handle_knowledge_topic_completed(data: dict) -> None:
    """Create flashcard suggestions when a topic is completed.

    **The name was `knowledge.topic_completed`, which nothing has ever emitted.** The knowledge domain
    emits `topic.completed` — see `KnowledgeEvents.TOPIC_COMPLETED` — so this handler was listening for
    a name spoken nowhere in the codebase. Registered or not, it could never have fired.

    A second listener on the same name is fine: handlers are a list per event and `emit` gathers them,
    so this and `progress.listeners.schedule_first_review` both run, and a failure in one does not
    affect the other.
    """
    from .services import notification_service

    user_id = data.get("user_id")
    topic_id = data.get("topic_id")

    if not user_id or not topic_id:
        return

    await notification_service.create_notification(
        user_id=user_id,
        type="suggestion",
        title="Create flashcards?",
        body="You just completed a topic. Create flashcards to retain it long-term?",
        priority=4,
        action_data={"action": "generate_flashcards", "topic_id": topic_id},
    )
