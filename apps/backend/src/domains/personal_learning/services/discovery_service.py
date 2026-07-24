"""
Discovery service — proactive recommendations.

Helps learners discover relevant resources, topics, and connections
based on their current learning without searching.
"""

import json
import logging
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def get_recommendations(*, user_id: str, limit: int = 5) -> list[Any]:
    """
    Get active recommendations for the learner.

    Req 15.1: Return resources, topics, courses relevant to active goals.
    Req 15.2: Ranked by relevance to current focus.
    """
    return await repo.list_active_recommendations(user_id, limit=limit)


async def follow_recommendation(*, user_id: str, recommendation_id: str) -> None:
    """
    Record that the learner followed a recommendation.

    Req 15.4: Strengthen similar recommendations in future.
    """
    await repo.mark_followed(recommendation_id, user_id)


async def dismiss_recommendation(*, user_id: str, recommendation_id: str) -> None:
    """
    Record that the learner dismissed a recommendation.

    Req 15.3: Reduce similar recommendations in future.
    """
    await repo.dismiss_recommendation(recommendation_id, user_id)


async def generate_recommendations(*, user_id: str) -> int:
    """
    Generate fresh recommendations for a learner.
    Called by the daily background task.

    Req 15.5: Generate fresh recommendations daily via background task.

    Returns the count of new recommendations created.
    """
    from src.domains.intelligence.reasoning.llm import generate_content

    # Clean up old recommendations
    await repo.delete_old_recommendations(user_id)

    # Get learner context
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return 0

    # Build context
    subjects = profile.subjects or []
    purpose = profile.purpose or "general_learning"
    goals = profile.goals_text or ""

    prompt = (
        f"Based on this learner's profile, suggest 5 learning recommendations:\n"
        f"Purpose: {purpose}\n"
        f"Subjects: {', '.join(subjects) if subjects else 'not specified'}\n"
        f"Goals: {goals}\n\n"
        f"Return a JSON array of recommendation objects with:\n"
        f"- 'itemType': 'course', 'topic', or 'resource'\n"
        f"- 'itemId': a descriptive identifier\n"
        f"- 'title': short title\n"
        f"- 'reason': why this is relevant to the learner (one sentence)\n"
        f"- 'relevanceScore': 0.0 to 1.0\n\n"
        f"Return ONLY the JSON array."
    )

    try:
        response = await generate_content(prompt, max_tokens=1500)
        recs_data = json.loads(response)
    except Exception as e:
        logger.warning(f"Failed to generate recommendations for user {user_id}: {e}")
        return 0

    count = 0
    for rec in recs_data:
        if isinstance(rec, dict) and "title" in rec:
            await repo.create_recommendation(
                {
                    "userId": user_id,
                    "itemType": rec.get("itemType", "topic"),
                    "itemId": rec.get("itemId", rec.get("title", "")),
                    "title": rec["title"],
                    "reason": rec.get("reason", "Relevant to your current learning"),
                    "relevanceScore": min(max(float(rec.get("relevanceScore", 0.5)), 0.0), 1.0),
                    "status": "ACTIVE",
                }
            )
            count += 1

    return count
