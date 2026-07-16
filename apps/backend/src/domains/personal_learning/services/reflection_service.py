"""
Reflection service — AI-generated progress summaries.

Uses the Three Layer Model (Activities -> Progress -> Achievements) to help
learners see how far they've come and identify areas to improve.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def generate_reflection(*, user_id: str, type: str) -> Any:
    """
    Generate an AI reflection (weekly or monthly).

    Req 12.1: Weekly — topics studied, time invested, retention improvements, accomplishments.
    Req 12.2: Monthly — compare to previous months, growth trends, patterns.
    Req 12.3: Frame using Three Layer Model (Activities, Progress, Achievements).
    Req 12.4: Include prescriptive recommendations; deliver without if LLM fails.
    """
    from src.domains.intelligence.reasoning.llm import generate_content

    now = datetime.now(timezone.utc)

    # Determine period
    if type == "weekly":
        period_start = now - timedelta(days=7)
    elif type == "monthly":
        period_start = now - timedelta(days=30)
    else:
        period_start = now - timedelta(days=7)

    period_end = now

    # Gather data for the reflection (from profile and recent activity)
    profile = await repo.get_profile_by_user(user_id)

    # Build context for LLM
    context = (
        f"Period: {period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}\n"
        f"Type: {type}\n"
        f"Learner profile: purpose={getattr(profile, 'purpose', 'unknown')}, "
        f"consistency_score={getattr(profile, 'consistency_score', 'N/A')}, "
        f"avg_session_minutes={getattr(profile, 'avg_session_minutes', 'N/A')}, "
        f"streak days (maturity)={getattr(profile, 'maturity_days', 0)}"
    )

    prompt = (
        f"Generate a learning reflection for this period:\n{context}\n\n"
        f"Structure the reflection using three layers:\n"
        f"1. ACTIVITIES: What the learner did (topics studied, sessions completed, notes created)\n"
        f"2. PROGRESS: What changed because of those activities (concepts mastered, consistency improved, knowledge retained)\n"
        f"3. ACHIEVEMENTS: What milestones were reached (streaks, completions, goals met)\n\n"
        f"Return a JSON object with:\n"
        f"- 'summary': 2-3 paragraph narrative reflection (encouraging, specific)\n"
        f"- 'activitiesLayer': {{\"topics_studied\": int, \"sessions_completed\": int, \"notes_created\": int, \"total_minutes\": float}}\n"
        f"- 'progressLayer': {{\"concepts_mastered\": int, \"consistency_change\": str, \"retention_score\": str}}\n"
        f"- 'achievementsLayer': {{\"milestones\": [str], \"streak_days\": int}}\n"
        f"- 'recommendations': [str] (2-4 prescriptive next steps)\n\n"
        f"Return ONLY the JSON object."
    )

    # Default layers if LLM fails (Req 12.3: all three layers always present)
    activities_layer = {"topics_studied": 0, "sessions_completed": 0, "notes_created": 0, "total_minutes": 0.0}
    progress_layer = {"concepts_mastered": 0, "consistency_change": "stable", "retention_score": "N/A"}
    achievements_layer = {"milestones": [], "streak_days": getattr(profile, "maturity_days", 0) or 0}
    summary = f"Your {type} learning reflection is ready. Keep going!"
    recommendations = None

    try:
        response = await generate_content(prompt, max_tokens=2000)
        data = json.loads(response)
        summary = data.get("summary", summary)
        activities_layer = data.get("activitiesLayer", activities_layer)
        progress_layer = data.get("progressLayer", progress_layer)
        achievements_layer = data.get("achievementsLayer", achievements_layer)
        recommendations = data.get("recommendations")
    except Exception as e:
        # Req 12.4: Deliver reflection even if recommendation generation fails
        logger.warning(f"LLM reflection generation failed for user {user_id}: {e}")

    # Store the reflection
    reflection = await repo.create_reflection({
        "userId": user_id,
        "type": type,
        "periodStart": period_start,
        "periodEnd": period_end,
        "summary": summary,
        "activitiesLayer": activities_layer,
        "progressLayer": progress_layer,
        "achievementsLayer": achievements_layer,
        "recommendations": recommendations,
    })

    return reflection


async def list_reflections(
    *, user_id: str, type_filter: str | None = None, page: int = 1, page_size: int = 20
) -> tuple[list[Any], int]:
    """
    List past reflections.
    Req 12.5: Sorted by date with summary previews.
    """
    skip = (page - 1) * page_size
    return await repo.list_reflections(user_id, type_filter=type_filter, skip=skip, take=page_size)


async def get_reflection(*, user_id: str, reflection_id: str) -> Any:
    """Get a single reflection."""
    reflection = await repo.get_reflection(reflection_id, user_id)
    if not reflection:
        raise NotFoundError("Reflection", reflection_id)
    return reflection
