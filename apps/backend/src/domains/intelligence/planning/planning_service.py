"""
Planning service â€” schedule generation and learning recommendations.

Plans learning activities based on user goals, progress, and behaviour.
Generates study schedules, revision recommendations, and session suggestions.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def generate_study_plan(*, user_id: str, course_id: str | None = None) -> dict[str, Any]:
    """Generate an AI-powered study plan.

    Considers: course progress, upcoming deadlines, learning patterns,
    spaced repetition schedule, and available time.
    """
    from src.domains.intelligence.planning.planning_impl import generate_study_plan as _plan

    return await _plan(user_id=user_id, course_id=course_id)


async def generate_schedule(*, user_id: str, preferences: dict[str, Any] | None = None) -> Any:
    """Generate a full weekly study schedule.

    Uses AI to distribute study blocks across the week based on
    courses, goals, and user preferences.
    """
    from src.domains.intelligence.planning.schedule_regen_impl import regenerate_schedule

    return await regenerate_schedule(user_id=user_id, preferences=preferences or {})


# `get_recommendations` is deleted along with `GET /api/v1/intelligence/recommendations`.
#
# It imported `get_learning_insights` from `planning/reflection_impl.py`, which defines only
# `evaluate_action_outcome` and `build_reflection_context`, so the call could never have run. The body
# was a comment reading "Future: build recommendation engine from observation + memory" above a
# delegation to a function that did not exist — an intention, not an implementation.
#
# Restore it when there is a recommendation engine to delegate to. `action/skills/handlers`'
# `handle_get_learning_insights` reads real `LearningInsight` rows and is the obvious starting point,
# but it is a tool handler with a tool handler's signature and return shape, and adapting it is work
# rather than a rename.
