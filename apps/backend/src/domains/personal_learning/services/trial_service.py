"""
Trial Service — Manages the 7-day PLUS trial lifecycle.

Allows free users to experience PLUS capabilities temporarily.
Handles start, status checks, showcase suggestions, summary generation,
and graceful expiry (no data loss).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.shared.database.session import get_session

logger = logging.getLogger(__name__)

# ===========================================================================
# Constants
# ===========================================================================

TRIAL_DURATION_DAYS = 7
TRIAL_COOLDOWN_DAYS = 180  # Can only trial once per 6 months


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class TrialStatus:
    """Current trial state for a user."""

    is_active: bool = False
    day_number: int = 0  # 1-7 during trial
    days_remaining: int = 0
    started_at: datetime | None = None
    ends_at: datetime | None = None
    expired: bool = False
    next_trial_available_at: datetime | None = None  # If cooldown active


@dataclass
class ShowcaseSuggestion:
    """A PLUS capability to showcase during trial."""

    capability_id: str
    title: str
    description: str
    action_url: str
    reason: str  # Why this is suggested for this user


@dataclass
class TrialSummary:
    """Post-trial summary showing value received."""

    trial_days: int
    plus_features_used: list[str]
    learning_outcomes: list[str]
    what_you_would_lose: list[str]
    upgrade_url: str = "/subscription"


# ===========================================================================
# Service Functions
# ===========================================================================


async def start_trial(user_id: str) -> TrialStatus:
    """
    Start a 7-day PLUS trial for a free user.

    Raises ValueError if:
    - User is already on trial
    - User has trialed within the last 180 days
    - User is already a PLUS subscriber
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository
    from . import feature_tier_service

    repo = PersonalLearningRepository()

    # Check if user is already PLUS
    tier, is_trial, _ = await feature_tier_service.get_effective_tier(user_id)
    if tier == "plus" and not is_trial:
        raise ValueError("You're already a Plus subscriber — no trial needed!")

    # Get or create profile
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        raise ValueError("Learning profile not found. Complete onboarding first.")

    now = datetime.now(timezone.utc)

    # Check active trial
    if profile.trial_ends_at and now < profile.trial_ends_at:
        raise ValueError("You already have an active trial.")

    # Check cooldown
    if profile.last_trial_ended_at:
        days_since = (now - profile.last_trial_ended_at).days
        if days_since < TRIAL_COOLDOWN_DAYS:
            next_available = profile.last_trial_ended_at + timedelta(days=TRIAL_COOLDOWN_DAYS)
            raise ValueError(
                f"Trial available again on {next_available.strftime('%B %d, %Y')}. "
                f"You can trial once every {TRIAL_COOLDOWN_DAYS} days."
            )

    # Start the trial
    ends_at = now + timedelta(days=TRIAL_DURATION_DAYS)
    update_data = {
        "trialStartedAt": now,
        "trialEndsAt": ends_at,
    }
    await repo.update_profile(user_id, update_data)

    logger.info(f"Trial started for user {user_id}, ends at {ends_at}")

    return TrialStatus(
        is_active=True,
        day_number=1,
        days_remaining=TRIAL_DURATION_DAYS,
        started_at=now,
        ends_at=ends_at,
    )


async def get_trial_status(user_id: str) -> TrialStatus | None:
    """
    Get current trial status for a user.

    Returns None if user has never interacted with trials.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        return None

    now = datetime.now(timezone.utc)

    # Active trial
    if profile.trial_ends_at and now < profile.trial_ends_at:
        elapsed = (now - profile.trial_started_at).days if profile.trial_started_at else 0
        day_number = min(elapsed + 1, TRIAL_DURATION_DAYS)
        days_remaining = max(0, (profile.trial_ends_at - now).days)
        return TrialStatus(
            is_active=True,
            day_number=day_number,
            days_remaining=days_remaining,
            started_at=profile.trial_started_at,
            ends_at=profile.trial_ends_at,
        )

    # Expired trial
    if profile.last_trial_ended_at:
        days_since = (now - profile.last_trial_ended_at).days
        next_available = None
        if days_since < TRIAL_COOLDOWN_DAYS:
            next_available = profile.last_trial_ended_at + timedelta(days=TRIAL_COOLDOWN_DAYS)
        return TrialStatus(
            is_active=False,
            expired=True,
            started_at=profile.trial_started_at,
            ends_at=profile.trial_ends_at,
            next_trial_available_at=next_available,
        )

    # Never trialed — check if eligible
    return TrialStatus(is_active=False)


async def get_showcase_suggestions(user_id: str) -> list[ShowcaseSuggestion]:
    """
    Suggest PLUS capabilities to try during the trial.

    Returns up to 3 suggestions based on what the user is currently doing
    and what PLUS features they haven't tried yet.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    used_features = set(profile.plus_features_used_this_period or []) if profile else set()
    suggestions: list[ShowcaseSuggestion] = []

    # Suggest capabilities the user hasn't tried yet
    showcase_options = [
        ShowcaseSuggestion(
            capability_id="quiz_modes",
            title="Try Adaptive Quizzes",
            description="Quizzes that adjust difficulty based on your performance — focus on what you need most.",
            action_url="/learning/preparations",
            reason="Adaptive quizzes help you identify and strengthen weak areas faster",
        ),
        ShowcaseSuggestion(
            capability_id="document_generation",
            title="Generate a Presentation",
            description="Create a polished PPTX presentation from your notes or a topic description.",
            action_url="/learning/documents",
            reason="Turn your knowledge into shareable presentations",
        ),
        ShowcaseSuggestion(
            capability_id="study_plan",
            title="Adaptive Study Plan",
            description="Get a study plan that automatically adjusts based on your quiz scores and patterns.",
            action_url="/learning/study-plans",
            reason="Plans that adapt as you learn help you stay on track",
        ),
        ShowcaseSuggestion(
            capability_id="reflection",
            title="Deep Learning Reflection",
            description="Get cross-topic pattern analysis with specific actionable recommendations.",
            action_url="/learning/reflections",
            reason="Understand how your learning connects across different areas",
        ),
        ShowcaseSuggestion(
            capability_id="flashcard_generation",
            title="Advanced Flashcards",
            description="Generate cloze, multiple-choice, and image-based flashcards from your notes.",
            action_url="/learning/flashcards",
            reason="Varied card types improve retention through different recall mechanisms",
        ),
        ShowcaseSuggestion(
            capability_id="behaviour_analytics",
            title="Predictive Study Scheduling",
            description="See your optimal study times and get proactive support for consistency.",
            action_url="/learning/behaviour/profile",
            reason="Learn when your brain works best",
        ),
    ]

    for option in showcase_options:
        if option.capability_id not in used_features and len(suggestions) < 3:
            suggestions.append(option)

    return suggestions


async def generate_trial_summary(user_id: str) -> TrialSummary:
    """
    Generate a personalised trial summary after trial expiry.

    Shows what PLUS features were used, learning outcomes they contributed to,
    and what the user would lose by not upgrading.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository
    from . import feature_tier_service

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        return TrialSummary(
            trial_days=TRIAL_DURATION_DAYS,
            plus_features_used=[],
            learning_outcomes=[],
            what_you_would_lose=[],
        )

    features_used = profile.plus_features_used_this_period or []

    # Map features to learning outcomes
    outcomes = []
    losses = []
    for feature in features_used:
        matrix_entry = feature_tier_service.FEATURE_TIER_MATRIX.get(feature, {})
        plus_desc = matrix_entry.get("plus", {}).get("description", "")
        if plus_desc:
            outcomes.append(plus_desc)
            losses.append(matrix_entry.get("upgrade_value", ""))

    # Remove empty strings
    outcomes = [o for o in outcomes if o]
    losses = [l for l in losses if l]

    return TrialSummary(
        trial_days=TRIAL_DURATION_DAYS,
        plus_features_used=features_used,
        learning_outcomes=outcomes,
        what_you_would_lose=losses,
    )


async def expire_trial(user_id: str) -> None:
    """
    Expire a trial that has passed its end date.

    Gracefully degrades PLUS features back to FREE levels without data loss.
    Records the expiry for cooldown enforcement.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    now = datetime.now(timezone.utc)

    update_data = {
        "lastTrialEndedAt": now,
        # Don't clear trial_started_at/trial_ends_at — keep for historical reference
    }
    await repo.update_profile(user_id, update_data)

    logger.info(f"Trial expired for user {user_id}")


async def record_plus_feature_used(user_id: str, feature_id: str) -> None:
    """
    Record that a user used a PLUS feature (during trial or subscription).

    Used for value summaries and trial summaries.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return

    current = profile.plus_features_used_this_period or []
    if feature_id not in current:
        current.append(feature_id)
        await repo.update_profile(user_id, {"plusFeaturesUsedThisPeriod": current})
