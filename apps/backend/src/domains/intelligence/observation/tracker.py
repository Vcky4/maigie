"""
Observation — activity tracking and domain event listeners.

Intelligence observes the learning environment through domain events.
Every significant learning action (topic completed, session joined,
resource viewed) feeds into observation, which informs memory,
reasoning, and planning.
"""

import logging

from src.shared.events import listen

logger = logging.getLogger(__name__)


@listen("topic.completed")
async def on_topic_completed(data: dict) -> None:
    """Observe when a learner completes a topic."""
    user_id = data.get("user_id")
    topic_id = data.get("topic_id")
    logger.debug(f"Intelligence observed: topic.completed user={user_id} topic={topic_id}")
    # Future: update learning velocity, trigger spaced rep scheduling


@listen("course.created")
async def on_course_created(data: dict) -> None:
    """Observe when a new course is created."""
    user_id = data.get("user_id")
    course_id = data.get("course_id")
    logger.debug(f"Intelligence observed: course.created user={user_id} course={course_id}")
    # Future: analyze course structure, prepare learning path


@listen("user.registered")
async def on_user_registered(data: dict) -> None:
    """Observe when a new user registers."""
    user_id = data.get("user_id")
    logger.debug(f"Intelligence observed: user.registered user={user_id}")
    # Future: initialize learner profile, prepare onboarding context


@listen("space.member_joined")
async def on_member_joined(data: dict) -> None:
    """Observe when someone joins a Learning Space."""
    user_id = data.get("user_id")
    space_id = data.get("space_id")
    logger.debug(f"Intelligence observed: space.member_joined user={user_id} space={space_id}")
    # Future: recommend introductions, suggest relevant classrooms


@listen("progress.study_session_completed")
async def on_study_session_completed(data: dict) -> None:
    """Observe when a study session ends."""
    user_id = data.get("user_id")
    logger.debug(f"Intelligence observed: study_session_completed user={user_id}")
    # Future: update learning patterns, adjust recommendations


async def record_activity(user_id: str, **kwargs) -> None:
    """Record user activity (streak tracking, lastSeenAt)."""
    pass  # TODO: migrate implementation from services/activity_tracker
