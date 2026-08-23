"""
Home service — personalized learning home aggregation.

"What does this learner need next?"

Aggregates from profile, progress, flashcards, schedules, and discovery
to create the emotional center of the product. A Home, not a dashboard.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
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

    # Get progress summary. The week figures are read here rather than inside the builder so that
    # stays pure — and because both need the learner's own week, which is a query, not a constant.
    week_minutes, week_topics = await _week_so_far(user_id)
    progress_summary = _build_progress_summary(
        profile, flashcard_stats, weekly_minutes=week_minutes, topics_completed=week_topics
    )

    # Get due reviews and schedule blocks concurrently
    due_flashcards, schedule_blocks = await asyncio.gather(
        flashcard_service.get_due_flashcards(user_id=user_id),
        _get_schedule_blocks(user_id),
    )

    due_reviews = _build_due_reviews(due_flashcards)

    # Build a contextual greeting that matches todaysFocus
    todays_focus = guidance.get("todaysFocus")
    greeting = _build_contextual_greeting(profile, todays_focus, due_flashcards)

    return {
        "greeting": greeting,
        "todaysFocus": todays_focus,
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


def _build_contextual_greeting(
    profile: Any | None, todays_focus: dict | None, due_flashcards: list
) -> str:
    """Build a greeting message that matches today's focus.

    This is displayed on the hero card (not the page heading), so it should
    describe what the learner should do today rather than just say hello.
    """
    if due_flashcards:
        count = len(due_flashcards)
        return (
            f"You have {count} flashcard{'s' if count != 1 else ''} ready for review. "
            "Quick recall keeps knowledge fresh."
        )

    if todays_focus:
        reason = todays_focus.get("reason", "")
        title = todays_focus.get("title", "")
        if reason:
            return reason
        if title:
            return f"Continue with {title}. Every step builds understanding."

    # Fallback — general encouragement
    return "Every small step is part of your progress."


def _build_greeting(profile: Any | None, behaviour: dict | None) -> str:
    """
    Build personalized greeting based on time of day and context.

    Req 1.1: Greeting based on time of day, learner name, and current context
    (streak milestone, recent achievement, or encouragement).
    """
    now = datetime.now(UTC)
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
        today = datetime.now(UTC).date()
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


async def _week_so_far(user_id: str) -> tuple[float | None, int]:
    """`(weeklyMinutes, topicsCompletedThisWeek)` since the learner's own week began.

    **These two were hardcoded `None` and `0`.** They were published on every home response with a
    comment saying they would stay that way "until a real study-session and topic-completion source is
    introduced". Both sources exist, so a client reading `topicsCompletedThisWeek` was being told zero
    about a learner who had completed five.

    The window is Monday 00:00 in the learner's timezone, converted back to instants for the query —
    both columns are `timestamptz`, and comparing them against a local wall clock would shift the week
    by the learner's offset. Same construction as `course_service` and `study_plan_service`, so the
    three cannot disagree about which week it is.

    `weeklyMinutes` is measured desk time from `StudySession.duration`, which is minutes. It stays
    **`None` for a learner who has never recorded a session at all**, and is `0.0` for one who records
    them and did nothing this week — a different statement, and the one that Decision I exists for.
    Almost nothing writes `StudySession` today, so `None` is the common answer; publishing `0` would
    read as "you studied nothing" rather than "nothing was measured".
    """
    from src.domains.knowledge.repository import knowledge_repo
    from src.domains.progress.repository import progress_repo
    from src.shared.time import resolve_learner_timezone, to_learner_local

    learner_timezone = await resolve_learner_timezone(user_id)
    now_local = to_learner_local(datetime.now(UTC), learner_timezone)
    week_start_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # The instant the learner's week began, for the queries. Both reads are then narrowed in the
    # learner's own frame, so this only has to be early enough — not exact.
    week_start = week_start_local.astimezone(UTC)
    week_local_end = week_start_local + timedelta(days=7)

    try:
        completions = await knowledge_repo.completed_topic_dates(
            user_id, since=week_start - timedelta(days=1)
        )
        # `completed_topic_dates` already excludes undated completions, so a topic finished before
        # `completedAt` existed is not dated into this week. Compared in the learner's frame for the
        # same reason as the sessions below: it tolerates a naive column and it puts a late-Sunday
        # completion in the week the learner actually did it.
        topics_completed = sum(
            1
            for when in completions
            if week_start_local <= to_learner_local(when, learner_timezone) < week_local_end
        )
    except Exception:  # pragma: no cover - a failed read must not cost the whole home response
        logger.warning("home: topic completions unavailable", exc_info=True)
        topics_completed = 0

    weekly_minutes: float | None = None
    try:
        # A day wider on the near side, then filtered precisely in the learner's own frame below, so a
        # session late on Sunday local time is not dropped by a UTC boundary.
        week_sessions = await progress_repo.list_sessions(
            user_id, since=week_start - timedelta(days=1)
        )
        # **Through `to_learner_local`, not a raw comparison.** `StudySession.startTime` is
        # `timestamp without time zone` in Postgres while the ORM declares `DateTime(timezone=True)`,
        # so asyncpg returns naive datetimes and `start_time < week_end` raises
        # `TypeError: can't compare offset-naive and offset-aware datetimes` — the same mismatch that
        # made `GET /progress/goals` a 500. That helper already reads a naive value as UTC, which is how
        # these columns are written, so the rule lives in one place rather than being restated here.
        in_window = [
            session
            for session in week_sessions
            if session.start_time
            and week_start_local
            <= to_learner_local(session.start_time, learner_timezone)
            < week_local_end
        ]
        if in_window:
            # `StudySession.duration` is minutes, unlike `QuizSession.durationSeconds`.
            weekly_minutes = round(sum(float(s.duration or 0.0) for s in in_window), 1)
        else:
            # Only reached when the week is empty: distinguishes "tracks time, none this week" from
            # "never tracked time". `StudySession` is small and indexed on `(userId, startTime)`.
            weekly_minutes = 0.0 if await progress_repo.list_sessions(user_id) else None
    except Exception:  # pragma: no cover
        logger.warning("home: study sessions unavailable", exc_info=True)
        weekly_minutes = None

    return weekly_minutes, topics_completed


def _build_progress_summary(
    profile: Any | None,
    flashcard_stats: dict,
    *,
    weekly_minutes: float | None = None,
    topics_completed: int = 0,
) -> dict[str, Any]:
    """Build a truthful progress summary from persisted learner activity."""
    avg_minutes = getattr(profile, "avg_session_minutes", None)
    consistency = getattr(profile, "consistency_score", None)

    return {
        "currentStreak": flashcard_stats.get("currentStreak", 0),
        "activeDaysThisWeek": flashcard_stats.get("activeDaysThisWeek", []),
        "cardsReviewedThisWeek": flashcard_stats.get("reviewedThisWeek", 0),
        "cardsReviewedTotal": flashcard_stats.get("reviewedTotal", 0),
        "cardsMastered": flashcard_stats.get("masteredCount", 0),
        "totalCards": flashcard_stats.get("total", 0),
        "dueCards": flashcard_stats.get("dueToday", 0),
        "consistencyScore": round(float(consistency), 1) if consistency is not None else None,
        "averageSessionMinutes": round(float(avg_minutes), 1) if avg_minutes is not None else None,
        "weeklyMinutes": weekly_minutes,
        "topicsCompletedThisWeek": topics_completed,
    }


def _build_due_reviews(due_flashcards: list) -> list[dict[str, Any]]:
    """Build due review items directly from persisted flashcard state."""
    reviews = []
    for card in due_flashcards[:10]:
        overdue_hours = 0
        if card.next_review_at:
            delta = datetime.now(UTC) - card.next_review_at
            overdue_hours = int(delta.total_seconds() / 3600)
        deck = getattr(card, "deck", None)
        reviews.append(
            {
                "id": card.id,
                "type": "flashcard",
                "title": card.front[:80],
                "dueAt": card.next_review_at.isoformat() if card.next_review_at else None,
                "urgency": max(1, min(10, overdue_hours // 24 + 1)),
                "deckId": card.deck_id,
                "deckTitle": getattr(deck, "title", None),
                "repetitionCount": card.repetition_count,
                "intervalDays": card.interval_days,
                "lastReviewedAt": (
                    card.last_reviewed_at.isoformat() if card.last_reviewed_at else None
                ),
            }
        )
    return reviews


async def _get_schedule_blocks(user_id: str) -> list[dict[str, Any]]:
    """
    Get today's schedule blocks from study plans.

    Req 1.5: Upcoming schedule blocks for the current day.
    """
    today = datetime.now(UTC).date()
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
                    item_type = getattr(item, "item_type", "STUDY") or "STUDY"
                    blocks.append(
                        {
                            "id": item.id,
                            "title": item.title,
                            "startAt": item.scheduled_date.isoformat(),
                            "endAt": (
                                item.scheduled_date + timedelta(minutes=estimated_minutes)
                            ).isoformat(),
                            "type": item_type,
                            "actionData": {
                                "planId": plan.id,
                                "itemId": item.id,
                                "topicId": getattr(item, "topic_id", None),
                                "courseId": getattr(item, "course_id", None)
                                or getattr(plan, "course_id", None),
                                "type": item_type.lower(),
                            },
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
        days_away = (datetime.now(UTC) - last_active).days
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
