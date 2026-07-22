"""
Conversion Engine — Intelligent, behaviour-driven upgrade suggestions.

Evaluates conversion trigger conditions after learning actions and delivers
personalised premium suggestions at appropriate moments.

Core principles:
- Never pushy: 72h cooldown between triggers, 14-day backoff after 3 dismissals
- Never premature: No triggers in first 3 days of activity
- Always relevant: Triggers tied to demonstrated behaviour, not arbitrary timing
- Always dismissable: User can permanently suppress specific trigger types
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.shared.database.session import get_session_factory

logger = logging.getLogger(__name__)


# ===========================================================================
# Constants
# ===========================================================================

COOLDOWN_HOURS = 72  # Minimum hours between any two triggers
BACKOFF_AFTER_DISMISSALS = 3  # After N dismissals, enter backoff period
BACKOFF_PERIOD_DAYS = 14  # How long to wait after hitting backoff threshold
MIN_MATURITY_DAYS = 3  # Don't trigger in first N days
SAME_TRIGGER_COOLDOWN_DAYS = 30  # Don't show same trigger type for N days after dismissal


# ===========================================================================
# Trigger Definitions
# ===========================================================================

CONVERSION_TRIGGERS: list[dict[str, Any]] = [
    {
        "id": "active_learner_discovery",
        "condition_type": "active_learner",
        "capability": "general_plus_overview",
        "message_template": (
            "You've been learning consistently for {days} days and created {artifacts} "
            "study materials. Maigie Plus could help you learn even more effectively."
        ),
        "thresholds": {"min_maturity_days": 7, "min_artifacts": 5},
    },
    {
        "id": "quiz_struggle_adaptive",
        "condition_type": "quiz_score_low",
        "capability": "quiz_modes",
        "message_template": (
            "Your last quiz scored {score}%. Adaptive quizzes in Plus automatically "
            "focus on your weak areas to help you improve faster."
        ),
        "thresholds": {"max_score": 70},
    },
    {
        "id": "document_power_user",
        "condition_type": "document_frequency",
        "capability": "document_generation",
        "message_template": (
            "You've generated {count} documents this week. Plus unlocks DOCX, PPTX, "
            "and additional styles to match any assignment format."
        ),
        "thresholds": {"min_docs_per_week": 3},
    },
    {
        "id": "consistent_studier_scheduling",
        "condition_type": "consistent_studier",
        "capability": "behaviour_analytics",
        "message_template": (
            "Your study habits are impressive — {consistency}% consistency. Plus can "
            "predict your optimal study times and adapt plans to your rhythm."
        ),
        "thresholds": {"min_consistency": 0.7, "min_sessions": 10},
    },
    {
        "id": "multi_prep_planning",
        "condition_type": "multi_prep",
        "capability": "study_plan",
        "message_template": (
            "Managing {prep_count} preparations at once takes planning. Plus adaptive "
            "plans can balance them intelligently based on your deadlines and performance."
        ),
        "thresholds": {"min_active_preps": 3},
    },
]


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class PremiumSuggestion:
    """A conversion trigger to show the user."""

    trigger_id: str
    message: str
    capability: str
    upgrade_url: str = "/subscription"
    dismissable: bool = True
    trial_available: bool = False


# ===========================================================================
# Service Functions
# ===========================================================================


async def evaluate_triggers(
    user_id: str, action_type: str, action_context: dict[str, Any] | None = None
) -> PremiumSuggestion | None:
    """
    Evaluate conversion triggers after a learning action.

    Returns a PremiumSuggestion if conditions are met, or None.
    Called after significant learning actions (quiz complete, note create, etc.)
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository
    from . import feature_tier_service

    repo = PersonalLearningRepository()

    # Only evaluate for FREE users
    tier, is_trial, _ = await feature_tier_service.get_effective_tier(user_id)
    if tier == "plus":
        return None

    # Get user's learning profile
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return None

    now = datetime.now(timezone.utc)

    # Check maturity gate (no triggers in first 3 days)
    if (profile.maturity_days or 0) < MIN_MATURITY_DAYS:
        return None

    # Check cooldown (72h since last trigger shown)
    if profile.last_trigger_shown_at:
        hours_since = (now - profile.last_trigger_shown_at).total_seconds() / 3600
        if hours_since < COOLDOWN_HOURS:
            return None

    # Check backoff (3 consecutive dismissals → wait 14 days)
    if (profile.trigger_dismissal_count or 0) >= BACKOFF_AFTER_DISMISSALS:
        if profile.last_trigger_dismissed_at:
            days_since_last_dismiss = (now - profile.last_trigger_dismissed_at).days
            if days_since_last_dismiss < BACKOFF_PERIOD_DAYS:
                return None
            # Backoff period passed — reset counter
            await repo.update_profile(user_id, {"triggerDismissalCount": 0})

    # Gather user state for condition evaluation
    user_state = await _gather_user_state(user_id, repo, action_context)

    # Check for recently dismissed trigger types
    recently_dismissed = await _get_recently_dismissed_triggers(user_id, now)

    # Evaluate each trigger
    for trigger in CONVERSION_TRIGGERS:
        trigger_id = trigger["id"]

        # Skip if this trigger type was recently dismissed
        if trigger_id in recently_dismissed:
            continue

        # Evaluate condition
        if _evaluate_condition(trigger, user_state):
            # Build personalised message
            message = _build_message(trigger, user_state)

            # Check if trial is available
            trial_available = await feature_tier_service._trial_available(user_id)

            # Record the impression
            await _record_trigger_shown(user_id, trigger_id, trigger["capability"], now, repo)

            return PremiumSuggestion(
                trigger_id=trigger_id,
                message=message,
                capability=trigger["capability"],
                trial_available=trial_available,
            )

    return None


async def record_dismissal(user_id: str, trigger_id: str) -> None:
    """Record that a user dismissed a conversion trigger."""
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    now = datetime.now(timezone.utc)

    # Update profile
    profile = await repo.get_profile_by_user(user_id)
    if profile:
        new_count = (profile.trigger_dismissal_count or 0) + 1
        await repo.update_profile(
            user_id,
            {
                "triggerDismissalCount": new_count,
                "lastTriggerDismissedAt": now,
            },
        )

    # Update the trigger log record
    await _mark_trigger_dismissed(user_id, trigger_id, now)

    logger.info(f"User {user_id} dismissed trigger '{trigger_id}'")


async def record_conversion(user_id: str) -> None:
    """Record that a user converted (upgraded) — mark the most recent trigger."""
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    now = datetime.now(timezone.utc)

    # Find the most recent trigger and mark it converted
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select, update
        from src.domains.personal_learning.db_models import ConversionTriggerLog

        stmt = (
            select(ConversionTriggerLog)
            .where(ConversionTriggerLog.user_id == user_id)
            .where(ConversionTriggerLog.converted_at.is_(None))
            .order_by(ConversionTriggerLog.shown_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        log_entry = result.scalar_one_or_none()
        if log_entry:
            log_entry.converted_at = now
            await session.commit()

    logger.info(f"User {user_id} converted — attributed to trigger")


# ===========================================================================
# Internal Helpers
# ===========================================================================


async def _gather_user_state(
    user_id: str, repo: Any, action_context: dict | None
) -> dict[str, Any]:
    """Gather user state needed for trigger condition evaluation."""
    profile = await repo.get_profile_by_user(user_id)

    # Count artifacts
    note_count = await repo.count_user_notes(user_id) if hasattr(repo, "count_user_notes") else 0
    flashcard_count = 0
    prep_count = 0

    try:
        preps = await repo.list_active_preparations(user_id)
        prep_count = len(preps) if preps else 0
    except Exception:
        pass

    # Get recent quiz score from action context
    last_quiz_score = None
    if action_context:
        last_quiz_score = action_context.get("quiz_score")

    # Document count this week
    docs_this_week = 0
    try:
        docs_this_week = await repo.count_documents_since(
            user_id, datetime.now(timezone.utc) - timedelta(days=7)
        )
    except (AttributeError, Exception):
        pass

    return {
        "maturity_days": profile.maturity_days if profile else 0,
        "artifact_count": note_count + flashcard_count,
        "note_count": note_count,
        "active_preps": prep_count,
        "last_quiz_score": last_quiz_score,
        "consistency_score": profile.consistency_score if profile else None,
        "avg_session_minutes": profile.avg_session_minutes if profile else None,
        "docs_this_week": docs_this_week,
    }


def _evaluate_condition(trigger: dict, user_state: dict) -> bool:
    """Evaluate a single trigger's condition against user state."""
    condition_type = trigger["condition_type"]
    thresholds = trigger["thresholds"]

    if condition_type == "active_learner":
        return (
            user_state.get("maturity_days", 0) >= thresholds["min_maturity_days"]
            and user_state.get("artifact_count", 0) >= thresholds["min_artifacts"]
        )

    elif condition_type == "quiz_score_low":
        score = user_state.get("last_quiz_score")
        return score is not None and score < thresholds["max_score"]

    elif condition_type == "document_frequency":
        return user_state.get("docs_this_week", 0) >= thresholds["min_docs_per_week"]

    elif condition_type == "consistent_studier":
        consistency = user_state.get("consistency_score")
        return (
            consistency is not None
            and consistency >= thresholds["min_consistency"]
        )

    elif condition_type == "multi_prep":
        return user_state.get("active_preps", 0) >= thresholds["min_active_preps"]

    return False


def _build_message(trigger: dict, user_state: dict) -> str:
    """Build personalised trigger message from template."""
    template = trigger["message_template"]
    try:
        return template.format(
            days=user_state.get("maturity_days", 0),
            artifacts=user_state.get("artifact_count", 0),
            score=user_state.get("last_quiz_score", 0),
            count=user_state.get("docs_this_week", 0),
            consistency=int((user_state.get("consistency_score", 0) or 0) * 100),
            prep_count=user_state.get("active_preps", 0),
        )
    except (KeyError, ValueError):
        # Fallback to a generic message
        return "Maigie Plus could help you learn more effectively. Try it free for 7 days."


async def _record_trigger_shown(
    user_id: str, trigger_id: str, capability: str, now: datetime, repo: Any
) -> None:
    """Record that a trigger was shown to the user."""
    # Update profile
    await repo.update_profile(user_id, {"lastTriggerShownAt": now})

    # Create log entry
    factory = get_session_factory()
    async with factory() as session:
        from src.domains.personal_learning.db_models import ConversionTriggerLog

        log = ConversionTriggerLog(
            id=__import__("uuid").uuid4().hex[:25],
            user_id=user_id,
            trigger_id=trigger_id,
            shown_at=now,
            capability_highlighted=capability,
        )
        session.add(log)
        await session.commit()


async def _get_recently_dismissed_triggers(user_id: str, now: datetime) -> set[str]:
    """Get trigger IDs that were dismissed within the last 30 days."""
    cutoff = now - timedelta(days=SAME_TRIGGER_COOLDOWN_DAYS)

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.domains.personal_learning.db_models import ConversionTriggerLog

        stmt = (
            select(ConversionTriggerLog.trigger_id)
            .where(ConversionTriggerLog.user_id == user_id)
            .where(ConversionTriggerLog.dismissed_at.isnot(None))
            .where(ConversionTriggerLog.dismissed_at >= cutoff)
        )
        result = await session.execute(stmt)
        return {row[0] for row in result.all()}


async def _mark_trigger_dismissed(user_id: str, trigger_id: str, now: datetime) -> None:
    """Mark the most recent instance of a trigger as dismissed."""
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.domains.personal_learning.db_models import ConversionTriggerLog

        stmt = (
            select(ConversionTriggerLog)
            .where(ConversionTriggerLog.user_id == user_id)
            .where(ConversionTriggerLog.trigger_id == trigger_id)
            .where(ConversionTriggerLog.dismissed_at.is_(None))
            .order_by(ConversionTriggerLog.shown_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        log_entry = result.scalar_one_or_none()
        if log_entry:
            log_entry.dismissed_at = now
            await session.commit()
