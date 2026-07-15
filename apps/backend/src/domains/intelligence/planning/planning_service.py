"""
Planning service — schedule generation and learning recommendations.

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
    from src.services.planning_service import generate_study_plan as _plan

    return await _plan(user_id=user_id, course_id=course_id)


async def generate_schedule(*, user_id: str, preferences: dict[str, Any] | None = None) -> Any:
    """Generate a full weekly study schedule.

    Uses AI to distribute study blocks across the week based on
    courses, goals, and user preferences.
    """
    from src.services.schedule_regeneration_service import regenerate_schedule

    return await regenerate_schedule(user_id=user_id, preferences=preferences or {})


async def get_recommendations(*, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Get proactive learning recommendations.

    May suggest: revision topics, collaboration opportunities,
    resources, schedule adjustments, or study sessions.
    """
    # Future: build recommendation engine from observation + memory
    # For now, delegate to reflection service which provides basic insights
    from src.services.reflection_service import get_learning_insights

    insights = await get_learning_insights(user_id)
    return insights[:limit] if insights else []
