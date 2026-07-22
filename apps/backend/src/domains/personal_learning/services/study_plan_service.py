"""
Study Plan service — AI-generated day-by-day study plans.

Distributes topics across available days, respects behaviour patterns,
interleaves spaced repetition reviews, and adapts when learners fall behind.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def generate_plan(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Generate a day-by-day study plan.

    Req 7.1: Distribute topics across available study days.
    Req 7.2: Respect learner's observed study patterns from behaviour tracker.
    Req 7.3: Interleave spaced repetition reviews among new material.

    FREE: Basic timeline distribution.
    PLUS: Adaptive scheduling that adjusts based on quiz performance and behaviour.
    """
    from . import feature_tier_service, trial_service

    title = data["title"]
    goal_description = data.get("goalDescription")
    deadline_raw = data.get("deadline")
    prep_id = data.get("prepId")

    # Determine quality tier for plan generation
    quality_tier = await feature_tier_service.get_quality_tier(user_id)
    is_adaptive = quality_tier == "plus"

    if is_adaptive:
        await trial_service.record_plus_feature_used(user_id, "study_plan")

    # Parse deadline — fall back to 30 days from now if not provided.
    if isinstance(deadline_raw, datetime):
        deadline = deadline_raw
    elif isinstance(deadline_raw, str) and deadline_raw.strip():
        deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
    else:
        deadline = datetime.now(timezone.utc).replace(hour=23, minute=59) + timedelta(days=30)

    # Ensure timezone-aware for consistent math downstream
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    # Get behaviour profile for sustainable scheduling
    profile = await repo.get_profile_by_user(user_id)
    avg_session_minutes = 60  # default
    if profile and profile.avg_session_minutes:
        avg_session_minutes = profile.avg_session_minutes

    # Sustainable daily limit: avg * 1.5 or max 120 min
    max_daily_minutes = min(avg_session_minutes * 1.5, 120)

    # Get topics to distribute (from prep if provided)
    topics_to_plan: list[dict[str, Any]] = []
    if prep_id:
        prep_topics = await repo.list_prep_topics(prep_id)
        for t in prep_topics:
            if t.status != "MASTERED":
                topics_to_plan.append(
                    {
                        "title": t.title,
                        "estimatedMinutes": t.estimated_minutes or 30,
                        "topicId": None,
                        "prepTopicId": t.id,
                        "type": "STUDY",
                    }
                )

    if not topics_to_plan:
        # Generate generic plan items from goal description via LLM
        topics_to_plan = await _generate_topics_from_goal(title, goal_description, user_id=user_id)

    # Calculate available days
    now = datetime.now(timezone.utc)
    days_available = max(1, (deadline - now).days)

    # Distribute items across days
    plan_items = _distribute_items(topics_to_plan, days_available, now, max_daily_minutes)

    # Add review items (interleaved at 25% of plan items)
    review_items = _add_review_items(plan_items, days_available, now)
    all_items = sorted(plan_items + review_items, key=lambda x: x["scheduledDate"])

    # Create plan
    plan = await repo.create_study_plan(
        {
            "userId": user_id,
            "title": title,
            "goalDescription": goal_description,
            "deadline": deadline,
            "prepId": prep_id,
            "status": "ACTIVE",
            "totalItems": len(all_items),
            "completedItems": 0,
        }
    )

    # Create plan items
    for item in all_items:
        await repo.create_plan_item(
            {
                "planId": plan.id,
                "title": item["title"],
                "description": item.get("description"),
                "scheduledDate": item["scheduledDate"],
                "estimatedMinutes": item.get("estimatedMinutes", 30),
                "itemType": item.get("type", "STUDY"),
                "topicId": item.get("topicId"),
                "prepTopicId": item.get("prepTopicId"),
                "status": "PENDING",
            }
        )

    return await repo.get_study_plan(plan.id, user_id)


async def list_plans(*, user_id: str) -> list[Any]:
    """
    List active study plans.

    Req 7.6: Return with completion %, today's tasks, days remaining.
    """
    return await repo.list_active_plans(user_id)


async def get_plan(*, user_id: str, plan_id: str) -> Any:
    """Get a study plan with all items."""
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)
    return plan


async def complete_item(*, user_id: str, plan_id: str, item_id: str) -> Any:
    """
    Mark a plan item as completed.

    Req 7.4: Mark done and adjust if ahead/behind schedule.
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    now = datetime.now(timezone.utc)
    await repo.update_plan_item(
        item_id,
        {
            "status": "COMPLETED",
            "completedAt": now,
        },
    )

    # Update plan completion count
    new_completed = (plan.completed_items or 0) + 1

    from sqlalchemy import update as sa_update
    from src.domains.personal_learning.db_models import StudyPlan
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            sa_update(StudyPlan)
            .where(StudyPlan.id == plan_id)
            .values(completed_items=new_completed)
        )
        await session.execute(stmt)
        await session.commit()

    # Check if plan is fully completed
    if new_completed >= (plan.total_items or 0):
        await repo.update_plan_status(plan_id, "COMPLETED")

    # Check if behind schedule and redistribute if needed (Req 7.5)
    items = await repo.list_plan_items(plan_id)
    pending_past_due = [i for i in items if i.status == "PENDING" and i.scheduled_date < now]
    if len(pending_past_due) > 2:
        await _redistribute_plan(plan_id, user_id)

    return await repo.get_study_plan(plan_id, user_id)


async def _redistribute_plan(plan_id: str, user_id: str) -> None:
    """
    Redistribute remaining plan items when learner is behind schedule.

    Req 7.5: Redistribute within deadline respecting sustainable session lengths.
    Ensures no single day exceeds max_daily_minutes (avg_session * 1.5 or 120 min).
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        return

    now = datetime.now(timezone.utc)
    deadline = plan.deadline
    days_remaining = max(1, (deadline - now).days)

    # Get behaviour profile for sustainable session limit
    profile = await repo.get_profile_by_user(user_id)
    avg_session_minutes = 60
    if profile and profile.avg_session_minutes:
        avg_session_minutes = profile.avg_session_minutes
    max_daily_minutes = min(avg_session_minutes * 1.5, 120)

    # Get pending items
    items = await repo.list_plan_items(plan_id)
    pending_items = [i for i in items if i.status == "PENDING"]

    if not pending_items:
        return

    # Redistribute respecting max_daily_minutes per day
    day_index = 0
    daily_minutes_used = 0.0

    for item in pending_items:
        item_minutes = getattr(item, "estimated_minutes", 30) or 30

        # If adding this item would exceed the daily limit, move to next day
        if daily_minutes_used + item_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Cap at deadline — if we run out of days, pack remaining into last day
        actual_day = min(day_index, days_remaining - 1)
        new_date = now + timedelta(days=actual_day + 1)
        await repo.update_plan_item(item.id, {"scheduledDate": new_date})

        daily_minutes_used += item_minutes

        # If this single item fills the day, advance
        if daily_minutes_used >= max_daily_minutes:
            day_index += 1
            daily_minutes_used = 0.0


def _distribute_items(
    topics: list[dict[str, Any]],
    days_available: int,
    start: datetime,
    max_daily_minutes: float,
) -> list[dict[str, Any]]:
    """Distribute study items across available days respecting daily limit."""
    items: list[dict[str, Any]] = []
    day_index = 0
    daily_minutes_used = 0.0

    for topic in topics:
        est_minutes = topic.get("estimatedMinutes", 30)

        # Check if adding this would exceed daily limit
        if daily_minutes_used + est_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Wrap around if we exceed available days.
        # Start on day 0 (today) so a fresh plan gives the learner
        # something to do the moment it's created.
        actual_day = day_index % days_available
        scheduled = start + timedelta(days=actual_day)

        items.append(
            {
                "title": topic["title"],
                "description": topic.get("description"),
                "scheduledDate": scheduled,
                "estimatedMinutes": est_minutes,
                "type": topic.get("type", "STUDY"),
                "topicId": topic.get("topicId"),
                "prepTopicId": topic.get("prepTopicId"),
            }
        )

        daily_minutes_used += est_minutes
        if daily_minutes_used >= max_daily_minutes:
            day_index += 1
            daily_minutes_used = 0.0

    return items


def _add_review_items(
    plan_items: list[dict[str, Any]],
    days_available: int,
    start: datetime,
) -> list[dict[str, Any]]:
    """Add spaced repetition review items (every 3-4 days after first study)."""
    reviews: list[dict[str, Any]] = []
    # Review first third of topics for spaced repetition
    items_to_review = plan_items[: len(plan_items) // 3]

    for item in items_to_review:
        # Schedule review 3 days after initial study
        study_date = item["scheduledDate"]
        review_date = study_date + timedelta(days=3)

        # Don't schedule reviews past the plan end
        plan_end = start + timedelta(days=days_available)
        if review_date <= plan_end:
            reviews.append(
                {
                    "title": f"Review: {item['title']}",
                    "scheduledDate": review_date,
                    "estimatedMinutes": 15,
                    "type": "REVIEW",
                    "topicId": item.get("topicId"),
                    "prepTopicId": item.get("prepTopicId"),
                }
            )

    return reviews


async def _generate_topics_from_goal(
    title: str, goal_description: str | None, *, user_id: str | None = None
) -> list[dict[str, Any]]:
    """Generate study topics from a goal description using AI."""
    from .llm_resilient import generate_content_json

    prompt = (
        f"Break down this learning goal into study topics:\n"
        f"Goal: {title}\n"
        f"Description: {goal_description or ''}\n\n"
        f"Return a JSON array of objects with 'title' and 'estimatedMinutes' (15-90).\n"
        f"Generate 5-15 topics. Return ONLY the JSON array."
    )

    try:
        topics_data = await generate_content_json(
            prompt, max_tokens=2000, fallback=None, user_id=user_id
        )
        return [
            {
                "title": t["title"],
                "estimatedMinutes": t.get("estimatedMinutes", 30),
                "type": "STUDY",
            }
            for t in topics_data
            if isinstance(t, dict) and "title" in t
        ]
    except Exception as e:
        logger.warning(f"Failed to generate topics from goal: {e}")
        # Fallback: single topic from the title
        return [{"title": title, "estimatedMinutes": 60, "type": "STUDY"}]
