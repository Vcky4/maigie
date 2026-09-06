"""
Observation — activity tracking and domain event listeners.

Intelligence observes the learning environment through domain events.
Every significant learning action (topic completed, session joined,
resource viewed) feeds into observation, which informs memory,
reasoning, and planning.
"""

import logging

from src.shared.events import (
    IdentityEvents,
    KnowledgeEvents,
    LearningSpaceEvents,
    ProgressEvents,
    listen,
)

logger = logging.getLogger(__name__)


@listen(KnowledgeEvents.TOPIC_COMPLETED)
async def on_topic_completed(data: dict) -> None:
    """Observe when a learner completes a topic."""
    user_id = data.get("user_id")
    topic_id = data.get("topic_id")
    logger.debug(f"Intelligence observed: topic.completed user={user_id} topic={topic_id}")
    # Future: update learning velocity, trigger spaced rep scheduling


@listen(KnowledgeEvents.COURSE_CREATED)
async def on_course_created(data: dict) -> None:
    """Observe when a new course is created."""
    user_id = data.get("user_id")
    course_id = data.get("course_id")
    logger.debug(f"Intelligence observed: course.created user={user_id} course={course_id}")
    # Future: analyze course structure, prepare learning path


@listen(IdentityEvents.USER_REGISTERED)
async def on_user_registered(data: dict) -> None:
    """Observe when a new user registers."""
    user_id = data.get("user_id")
    logger.debug(f"Intelligence observed: user.registered user={user_id}")
    # Future: initialize learner profile, prepare onboarding context


@listen(LearningSpaceEvents.MEMBER_JOINED)
async def on_member_joined(data: dict) -> None:
    """Observe when someone joins a Learning Space."""
    user_id = data.get("user_id")
    space_id = data.get("space_id")
    logger.debug(f"Intelligence observed: space.member_joined user={user_id} space={space_id}")
    # Future: recommend introductions, suggest relevant classrooms


@listen(ProgressEvents.STUDY_SESSION_COMPLETED)
async def on_study_session_completed(data: dict) -> None:
    """Observe when a study session ends."""
    user_id = data.get("user_id")
    logger.debug(f"Intelligence observed: study_session_completed user={user_id}")
    # Future: update learning patterns, adjust recommendations


async def record_activity(user_id: str, **_kwargs) -> None:
    """Record meaningful learner activity, updating the study streak.

    This was a ``pass``, so the chat path in ``websocket_handler`` recorded nothing and
    sending a message never counted towards a streak, while the analytics path called a
    working implementation directly. There is no second implementation to write: it
    already exists in ``progress.services.activity_tracker``, which owns the streak
    tables, so this delegates rather than duplicating the rules.

    Imported lazily to keep the intelligence domain from importing progress at module
    load; this module is imported for its event listeners at startup.
    """
    from src.domains.progress.services.activity_tracker import (
        record_activity as record_progress_activity,
    )

    await record_progress_activity(user_id)
