"""
Discovery service — proactive recommendations.

Helps learners discover relevant resources, topics, and connections
based on their current learning without searching.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
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


#: How often a fresh set is generated, by entitlement (Decision M).
#:
#: The nightly task fans out to everyone; this is what makes the *cadence* follow the tier. Free
#: weekly, Plus nightly — the same shape as the reflection split, where the deterministic summary is
#: the free version and the written narrative is the paid one.
#:
#: Nightly is expressed as zero rather than one day so "has a day passed" is not a question about
#: clock drift: a Plus learner is always due.
CADENCE_DAYS_FREE = 7
CADENCE_DAYS_PLUS = 0


async def _cadence_allows(user_id: str) -> bool:
    """Whether this learner is due a fresh set of recommendations.

    Fails **open**. A cadence gate that cannot read an entitlement or a timestamp should let the
    generation happen: the cost of one extra recommendation is ~150 units, and the cost of the
    opposite mistake is a learner whose discovery feed silently stops. Same posture as
    `_refuse_if_exhausted`, and the opposite of the model-quality gate, which fails to the cheap
    model because over-granting there is a margin question rather than a broken feature.
    """
    try:
        from src.domains.billing.services import entitlement_service

        entitlement = await entitlement_service.resolve(user_id)
        cadence_days = CADENCE_DAYS_PLUS if entitlement.tier == "plus" else CADENCE_DAYS_FREE
        if cadence_days <= 0:
            return True

        last_at = await repo.latest_recommendation_at(user_id)
        if last_at is None:
            return True
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        due = last_at + timedelta(days=cadence_days)
        if datetime.now(UTC) < due:
            logger.debug(
                "discovery: user=%s not due until %s (cadence=%dd)",
                user_id,
                due.isoformat(),
                cadence_days,
            )
            return False
        return True
    except Exception:
        logger.exception("discovery: cadence check failed for user=%s — generating", user_id)
        return True


async def generate_recommendations(*, user_id: str) -> int:
    """
    Generate fresh recommendations for a learner.
    Called by the daily background task.

    Req 15.5: Generate fresh recommendations daily via background task.

    Returns the count of new recommendations created.
    """
    # Through the chokepoint: this was a direct Gemini call, so discovery recommendations were
    # unmetered and ran the Plus model for everybody. Below the quality threshold at ~150 units, so
    # both tiers get Flash-Lite; what changes is that it is now charged and gated.
    from src.domains.personal_learning.services.llm_resilient import generate_content

    # **Cadence follows entitlement, not the calendar** (Decision M). The task runs nightly for
    # everyone; a free learner gets a fresh set weekly, a Plus learner nightly. Enforced here rather
    # than by two schedules, because a second Celery beat entry is a second thing that can drift from
    # the tier it is meant to track — and the tier can change between two runs of one schedule.
    #
    # Checked before the delete below, which matters: `delete_old_recommendations` is destructive, and
    # returning early *after* it would leave a free learner with nothing for six days rather than with
    # last week's set. The bug would look like the feature failing rather than like a cadence.
    if not await _cadence_allows(user_id):
        return 0

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
        response = await generate_content(
            prompt,
            max_tokens=1500,
            user_id=user_id,
            operation="discovery_recommendations",
        )
        recs_data = json.loads(response)
    except Exception as e:
        # A refusal lands here too, and being swallowed is the right outcome for once: this is a
        # background sweep with nobody waiting on it, and an exhausted learner should not have their
        # allowance spent on recommendations they did not ask for. It returns 0 and the next sweep
        # tries again.
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
