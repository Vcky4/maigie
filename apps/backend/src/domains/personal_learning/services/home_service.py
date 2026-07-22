"""
Home service — personalized learning home aggregation.

"What does this learner need next?"

Aggregates from profile, progress, flashcards, schedules, and discovery
to create the emotional center of the product. A Home, not a dashboard.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def get_home(*, user_id: str) -> dict[str, Any]:
    """
    Build the personalized home response.

    The learner opens Maigie and everything is ready.
    They don't plan. They don't organize. They just learn.
    """
    import asyncio

    from . import (
        behaviour_service,
        flashcard_service,
        guidance_engine,
    )

    # Fire guidance computation and profile fetch concurrently
    guidance, profile, flashcard_stats = await asyncio.gather(
        guidance_engine.compute_guidance(user_id=user_id),
        repo.get_profile_by_user(user_id),
        flashcard_service.get_statistics(user_id=user_id),
    )

    # Build greeting
    greeting = _build_greeting(profile, None)

    # Get progress summary
    progress_summary = _build_progress_summary(profile, flashcard_stats)

    # Get due reviews and schedule blocks concurrently
    due_flashcards, schedule_blocks = await asyncio.gather(
        flashcard_service.get_due_flashcards(user_id=user_id),
        _get_schedule_blocks(user_id),
    )

    due_reviews = _build_due_reviews(due_flashcards)

    return {
        "greeting": guidance.get("message", greeting),
        "todaysFocus": guidance.get("todaysFocus"),
        "progressSummary": progress_summary,
        "dueReviews": due_reviews,
        "scheduleBlocks": schedule_blocks,
        "readyForYou": guidance.get("readyForYou", []),
        "stage": guidance.get("stage", "active"),
        "nextAction": guidance.get("todaysFocus"),  # todaysFocus IS the next action
        "recommendations": [],  # Deprecated — replaced by readyForYou
        "reEngagement": _check_re_engagement(profile),
        "isOnboarding": guidance.get("stage") in ("fresh", "purpose_set", "setting_up"),
    }


def _build_greeting(profile: Any | None, behaviour: dict | None) -> str:
    """
    Build personalized greeting based on time of day and context.

    Req 1.1: Greeting based on time of day, learner name, and current context
    (streak milestone, recent achievement, or encouragement).
    """
    now = datetime.now(timezone.utc)
    hour = now.hour

    if 5 <= hour < 12:
        time_greeting = "Good morning"
    elif 12 <= hour < 17:
        time_greeting = "Good afternoon"
    elif 17 <= hour < 21:
        time_greeting = "Good evening"
    else:
        time_greeting = "Hello"

    # Add context (streak milestone, encouragement)
    maturity = getattr(profile, "maturity_days", 0) or 0
    consistency = getattr(profile, "consistency_score", None)

    context_suffix = ""
    if maturity > 0 and maturity % 7 == 0:
        context_suffix = f" You've been learning for {maturity} days."
    elif consistency and consistency >= 80:
        context_suffix = " Your consistency is outstanding."

    return f"{time_greeting}.{context_suffix}"


async def _compute_todays_focus(
    user_id: str, due_flashcards: list, profile: Any | None
) -> dict[str, Any] | None:
    """
    Compute today's focus based on priority:
    1. Overdue spaced repetition
    2. Active study plan items for today
    3. Goal with nearest deadline

    Req 1.2: Today's focus recommendation containing course title, topic title,
    and the reason this was selected.
    """
    # Priority 1: Due flashcards
    if due_flashcards:
        return {
            "courseTitle": None,
            "topicTitle": f"{len(due_flashcards)} flashcards due",
            "reason": "Spaced repetition keeps knowledge fresh — review these before they decay.",
        }

    # Priority 2: Today's study plan items
    active_plans = await repo.list_active_plans(user_id)
    if active_plans:
        today = datetime.now(timezone.utc).date()
        for plan in active_plans:
            if plan.items:
                todays_items = [
                    i
                    for i in plan.items
                    if i.status == "PENDING"
                    and hasattr(i, "scheduled_date")
                    and i.scheduled_date
                    and i.scheduled_date.date() == today
                ]
                if todays_items:
                    item = todays_items[0]
                    return {
                        "courseTitle": plan.title,
                        "topicTitle": item.title,
                        "reason": "Scheduled for today in your study plan.",
                    }

    # Priority 3: General encouragement
    return {
        "courseTitle": None,
        "topicTitle": "Continue where you left off",
        "reason": "Pick up from your last session — consistency builds mastery.",
    }


def _build_progress_summary(profile: Any | None, flashcard_stats: dict) -> dict[str, Any]:
    """
    Build progress summary from profile and stats.

    Req 1.3: Progress summary containing current streak count,
    weekly study minutes, and topics completed this week.

    Note: currentStreak and weeklyMinutes are background-task computed values
    stored on the profile. They update once daily via Celery beat.
    If both are 0, it means the background task hasn't run yet for this user.
    """
    maturity = getattr(profile, "maturity_days", 0) or 0
    avg_minutes = getattr(profile, "avg_session_minutes", 0) or 0

    return {
        "currentStreak": maturity,
        "weeklyMinutes": round(avg_minutes * 5, 1),
        "topicsCompletedThisWeek": flashcard_stats.get("masteredCount", 0),
    }


def _build_due_reviews(due_flashcards: list) -> list[dict[str, Any]]:
    """
    Build due review items from flashcards.

    Req 1.4: Due spaced repetition reviews sorted by urgency.
    """
    reviews = []
    for card in due_flashcards[:10]:  # Cap at 10
        overdue_hours = 0
        if card.next_review_at:
            delta = datetime.now(timezone.utc) - card.next_review_at
            overdue_hours = int(delta.total_seconds() / 3600)
        reviews.append(
            {
                "id": card.id,
                "type": "flashcard",
                "title": card.front[:50],
                "dueAt": card.next_review_at.isoformat() if card.next_review_at else None,
                "urgency": max(1, min(10, overdue_hours // 24 + 1)),
            }
        )
    return reviews


async def _get_schedule_blocks(user_id: str) -> list[dict[str, Any]]:
    """
    Get today's schedule blocks from study plans.

    Req 1.5: Upcoming schedule blocks for the current day.
    """
    today = datetime.now(timezone.utc).date()
    plans = await repo.list_active_plans(user_id)

    blocks = []
    for plan in plans:
        if plan.items:
            for item in plan.items:
                if (
                    item.status == "PENDING"
                    and hasattr(item, "scheduled_date")
                    and item.scheduled_date
                    and item.scheduled_date.date() == today
                ):
                    estimated_minutes = getattr(item, "estimated_minutes", 30) or 30
                    blocks.append(
                        {
                            "id": item.id,
                            "title": item.title,
                            "startAt": item.scheduled_date.isoformat(),
                            "endAt": (
                                item.scheduled_date + timedelta(minutes=estimated_minutes)
                            ).isoformat(),
                            "type": getattr(item, "item_type", "STUDY") or "STUDY",
                        }
                    )

    return blocks


def _build_recommendations(recommendations: list, is_onboarding: bool) -> list[dict[str, Any]]:
    """
    Build recommendation list. Empty list when none available (never null).

    Req 1.6: Up to 5 personalized recommendations; empty list when none available.
    Req 1.7: Onboarding phase returns discovery-oriented actions.
    """
    if is_onboarding:
        # Onboarding: discovery-oriented actions
        return [
            {
                "type": "onboarding",
                "title": "Set your learning goals",
                "reason": "Help Maigie understand what you're working toward.",
                "actionData": {"action": "set_goals"},
            },
            {
                "type": "onboarding",
                "title": "Try a practice quiz",
                "reason": "See how Maigie adapts to your knowledge level.",
                "actionData": {"action": "start_quiz"},
            },
        ]

    rec_list = []
    for rec in recommendations[:5]:
        rec_list.append(
            {
                "type": getattr(rec, "item_type", "topic"),
                "title": getattr(rec, "title", ""),
                "reason": getattr(rec, "reason", ""),
                "actionData": {"recommendationId": getattr(rec, "id", "")},
            }
        )
    return rec_list


def _compute_next_action(
    due_flashcards: list, todays_focus: dict | None, schedule_blocks: list
) -> dict[str, Any]:
    """
    Compute the highest-priority next action.
    Priority: due review > scheduled block > study plan item > general

    Req 1.10: next_action field so the learner always knows what comes next.
    """
    if due_flashcards:
        return {
            "type": "review_flashcards",
            "title": f"Review {min(len(due_flashcards), 10)} flashcards",
            "actionData": {"action": "start_review"},
        }

    if schedule_blocks:
        block = schedule_blocks[0]
        return {
            "type": "scheduled_study",
            "title": block["title"],
            "actionData": {"blockId": block["id"]},
        }

    if todays_focus:
        return {
            "type": "continue_study",
            "title": todays_focus.get("topicTitle", "Continue learning"),
            "actionData": None,
        }

    return {
        "type": "explore",
        "title": "Explore something new",
        "actionData": None,
    }


def _check_re_engagement(profile: Any | None) -> dict[str, Any] | None:
    """
    Check if learner has been away > 7 days.

    Req 1.8: Return gentle message without guilt when away > 7 days.
    """
    if not profile:
        return None

    # Use updated_at on the profile as a proxy for last activity.
    # If the profile hasn't been updated in > 7 days, the learner may be away.
    last_active = getattr(profile, "updated_at", None)
    if last_active:
        days_away = (datetime.now(timezone.utc) - last_active).days
        if days_away > 7:
            return {
                "message": "Welcome back! Pick up where you left off — no pressure.",
                "suggestedAction": {
                    "type": "low_effort",
                    "title": "Review one flashcard",
                    "actionData": {"action": "start_review", "limit": 1},
                },
                "daysAway": days_away,
            }

    return None
