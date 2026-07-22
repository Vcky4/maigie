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

from src.shared.database.session import get_session_factory

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

    Returns up to 3 suggestions personalised based on:
    - What the user is currently doing (active preps, notes, quizzes)
    - Their stated purpose (exam_prep → quizzes, skill_building → study plans)
    - What PLUS features they haven't tried yet
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    used_features = set(profile.plus_features_used_this_period or []) if profile else set()
    purpose = profile.purpose if profile else None

    # Count user's content to understand what they're doing
    note_count = 0
    prep_count = 0
    try:
        note_count = await repo.count_user_notes(user_id)
    except (AttributeError, Exception):
        pass
    try:
        preps = await repo.list_active_preparations(user_id)
        prep_count = len(preps) if preps else 0
    except (AttributeError, Exception):
        pass

    # Build personalised suggestions with priority scores
    scored_options: list[tuple[int, ShowcaseSuggestion]] = []

    if "quiz_modes" not in used_features:
        # Higher priority for exam_prep users or those with active preps
        score = 50
        if purpose in ("exam_prep", "professional_certification"):
            score = 100
        elif prep_count > 0:
            score = 80
        reason = (
            f"With your {'exam preparation' if prep_count > 0 else 'learning goals'}, "
            f"adaptive quizzes can target your weak areas automatically"
        )
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="quiz_modes",
            title="Try Adaptive Quizzes",
            description="Quizzes that adjust difficulty based on your performance — focusing where you need it most.",
            action_url="/learning/preparations",
            reason=reason,
        )))

    if "document_generation" not in used_features:
        score = 40
        if purpose in ("course_completion", "skill_building"):
            score = 70
        elif note_count >= 3:
            score = 60
        reason = (
            "Turn your notes into polished presentations or reports"
            if note_count > 0
            else "Generate professional documents from any topic description"
        )
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="document_generation",
            title="Generate a Presentation",
            description="Create a polished PPTX presentation from your notes or a topic description.",
            action_url="/learning/documents",
            reason=reason,
        )))

    if "study_plan" not in used_features:
        score = 45
        if prep_count > 0:
            score = 75
        elif purpose == "exam_prep":
            score = 70
        reason = (
            f"With {prep_count} active preparation{'s' if prep_count != 1 else ''}, "
            f"adaptive plans can balance your workload intelligently"
            if prep_count > 0
            else "Plans that adapt based on your quiz scores and study patterns"
        )
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="study_plan",
            title="Adaptive Study Plan",
            description="Get a study plan that automatically adjusts based on your quiz scores and patterns.",
            action_url="/learning/study-plans",
            reason=reason,
        )))

    if "reflection" not in used_features:
        score = 30
        if (profile and (profile.maturity_days or 0) >= 7):
            score = 65
        reason = (
            "After a week of learning, deep reflections can reveal patterns across your topics"
            if (profile and (profile.maturity_days or 0) >= 7)
            else "Understand how your learning connects across different areas"
        )
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="reflection",
            title="Deep Learning Reflection",
            description="Get cross-topic pattern analysis with specific actionable recommendations.",
            action_url="/learning/reflections",
            reason=reason,
        )))

    if "flashcard_generation" not in used_features:
        score = 35
        if note_count >= 2:
            score = 70
        reason = (
            f"You have {note_count} notes — advanced flashcards with cloze and multi-choice "
            f"can improve retention"
            if note_count > 0
            else "Varied card types improve retention through different recall mechanisms"
        )
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="flashcard_generation",
            title="Advanced Flashcards",
            description="Generate cloze, multiple-choice, and image-based flashcards from your notes.",
            action_url="/learning/flashcards",
            reason=reason,
        )))

    if "behaviour_analytics" not in used_features:
        score = 25
        if (profile and (profile.maturity_days or 0) >= 5):
            score = 55
        scored_options.append((score, ShowcaseSuggestion(
            capability_id="behaviour_analytics",
            title="Predictive Study Scheduling",
            description="See your optimal study times and get proactive support for consistency.",
            action_url="/learning/behaviour/profile",
            reason="Learn when your brain works best based on your actual study patterns",
        )))

    # Sort by score (highest first) and return top 3
    scored_options.sort(key=lambda x: x[0], reverse=True)
    return [suggestion for _, suggestion in scored_options[:3]]


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
