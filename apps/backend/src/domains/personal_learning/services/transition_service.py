"""
Transition Service — Learner-to-Educator journey detection and guidance.

Detects when a learner shows "educator readiness" signals and guides them
toward creating Learning Spaces. Manages the Circle Plan trial for qualified users.

Book principle: "Personal Learning Creates Future Communities" — some learners
begin teaching, some become mentors, some create Learning Spaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.shared.database.session import get_session

logger = logging.getLogger(__name__)


# ===========================================================================
# Constants
# ===========================================================================

EDUCATOR_READINESS_SIGNALS = {
    "maturity": {"threshold_days": 30},
    "content_created": {"notes_threshold": 10, "preps_threshold": 2},  # OR condition
    "quiz_performance": {"avg_score_threshold": 80},
    "plan_completion": {"rate_threshold": 70},
}

SIGNALS_REQUIRED = 3  # out of 4

CIRCLE_TRIAL_DURATION_DAYS = 7
CIRCLE_TRIAL_MAX_LEARNERS = 5
SUGGESTION_COOLDOWN_DAYS = 14

# Purposes that indicate personal-only intent (don't push educator transition)
PERSONAL_ONLY_PURPOSES = {"exam_prep", "general_learning"}


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class EducatorReadiness:
    """Evaluation of educator readiness signals."""

    is_ready: bool
    signals_met: int
    total_signals: int = 4
    signals: dict[str, bool] | None = None
    message: str | None = None


@dataclass
class TransitionSuggestion:
    """Suggestion to explore the educator path."""

    title: str
    message: str
    action_url: str
    circle_trial_available: bool


@dataclass
class CircleTrialStatus:
    """Status of the educator Circle Plan trial."""

    is_active: bool
    started_at: datetime | None = None
    ends_at: datetime | None = None
    spaces_created: int = 0
    max_learners: int = CIRCLE_TRIAL_MAX_LEARNERS


# ===========================================================================
# Service Functions
# ===========================================================================


async def evaluate_educator_readiness(user_id: str) -> EducatorReadiness:
    """
    Evaluate if a learner shows educator readiness signals.

    Checks 4 signals:
    1. maturity_days >= 30
    2. Created 10+ notes OR completed 2+ preparations
    3. Average quiz score >= 80%
    4. Study plan completion rate >= 70%
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        return EducatorReadiness(is_ready=False, signals_met=0)

    signals: dict[str, bool] = {}

    # Signal 1: Maturity
    signals["maturity"] = (profile.maturity_days or 0) >= EDUCATOR_READINESS_SIGNALS["maturity"]["threshold_days"]

    # Signal 2: Content created (notes >= 10 OR preps >= 2)
    note_count = 0
    prep_count = 0
    try:
        note_count = await repo.count_user_notes(user_id)
    except (AttributeError, Exception):
        pass
    try:
        preps = await repo.list_active_preparations(user_id)
        # Count completed preps
        prep_count = len([p for p in (preps or []) if getattr(p, "status", "") == "COMPLETED"])
    except (AttributeError, Exception):
        pass

    signals["content_created"] = (
        note_count >= EDUCATOR_READINESS_SIGNALS["content_created"]["notes_threshold"]
        or prep_count >= EDUCATOR_READINESS_SIGNALS["content_created"]["preps_threshold"]
    )

    # Signal 3: Quiz performance (average score >= 80%)
    avg_score = await _get_average_quiz_score(user_id)
    signals["quiz_performance"] = (
        avg_score is not None
        and avg_score >= EDUCATOR_READINESS_SIGNALS["quiz_performance"]["avg_score_threshold"]
    )

    # Signal 4: Study plan completion rate >= 70%
    completion_rate = await _get_plan_completion_rate(user_id)
    signals["plan_completion"] = (
        completion_rate is not None
        and completion_rate >= EDUCATOR_READINESS_SIGNALS["plan_completion"]["rate_threshold"]
    )

    signals_met = sum(1 for v in signals.values() if v)
    is_ready = signals_met >= SIGNALS_REQUIRED

    # Record readiness if newly met
    if is_ready and not profile.educator_readiness_met_at:
        await repo.update_profile(user_id, {"educatorReadinessMetAt": datetime.now(timezone.utc)})

    message = None
    if is_ready:
        message = (
            "You've shown strong learning capability. "
            "Some learners like you find they're ready to help others learn too."
        )

    return EducatorReadiness(
        is_ready=is_ready,
        signals_met=signals_met,
        signals=signals,
        message=message,
    )


async def get_transition_suggestion(user_id: str) -> TransitionSuggestion | None:
    """
    Get educator transition suggestion if appropriate.

    Returns None if:
    - User doesn't meet readiness threshold
    - User's purpose is purely personal
    - Suggestion was shown recently (within 14 days)
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        return None

    # Don't suggest for purely personal-purpose users
    if profile.purpose in PERSONAL_ONLY_PURPOSES:
        return None

    # Check readiness
    readiness = await evaluate_educator_readiness(user_id)
    if not readiness.is_ready:
        return None

    # Check suggestion cooldown
    now = datetime.now(timezone.utc)
    if profile.educator_suggestion_shown_at:
        days_since = (now - profile.educator_suggestion_shown_at).days
        if days_since < SUGGESTION_COOLDOWN_DAYS:
            return None

    # Record that we showed the suggestion
    await repo.update_profile(user_id, {"educatorSuggestionShownAt": now})

    # Check if circle trial is available
    circle_trial_available = profile.circle_trial_started_at is None

    return TransitionSuggestion(
        title="Share Your Knowledge",
        message=(
            "You've developed strong learning habits and deep knowledge. "
            "Have you considered helping others learn? You could create a "
            "Learning Space and invite learners to study together."
        ),
        action_url="/circles/create",
        circle_trial_available=circle_trial_available,
    )


async def start_circle_trial(user_id: str) -> CircleTrialStatus:
    """
    Start a 7-day Circle Plan trial for educator-ready users.

    Allows creating one Learning Space with up to 5 learners.
    Raises ValueError if user doesn't meet readiness or already trialed.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        raise ValueError("Learning profile not found.")

    # Verify educator readiness
    readiness = await evaluate_educator_readiness(user_id)
    if not readiness.is_ready:
        raise ValueError(
            f"You need to meet {SIGNALS_REQUIRED} of 4 educator readiness signals. "
            f"Currently at {readiness.signals_met}."
        )

    # Check if already trialed
    if profile.circle_trial_started_at:
        raise ValueError("You've already used your Circle Plan trial.")

    # Start the trial
    now = datetime.now(timezone.utc)
    await repo.update_profile(user_id, {"circleTrialStartedAt": now})

    # Track funnel event
    await track_transition_event(user_id, "trial_started")

    logger.info(f"Circle trial started for user {user_id}")

    return CircleTrialStatus(
        is_active=True,
        started_at=now,
        ends_at=now + timedelta(days=CIRCLE_TRIAL_DURATION_DAYS),
        max_learners=CIRCLE_TRIAL_MAX_LEARNERS,
    )


async def track_transition_event(user_id: str, event: str) -> None:
    """
    Track the educator transition funnel.

    Events: ready, suggested, explored, trial_started, converted
    """
    # For now, log the event. In future, could write to an analytics table.
    logger.info(f"Educator transition funnel: user={user_id} event={event}")


# ===========================================================================
# Internal Helpers
# ===========================================================================


async def _get_average_quiz_score(user_id: str) -> float | None:
    """Get average quiz score across all completed quizzes."""
    async with get_session() as session:
        from sqlalchemy import func, select
        from src.domains.personal_learning.db_models import QuizSession

        stmt = (
            select(func.avg(QuizSession.score_percentage))
            .where(QuizSession.user_id == user_id)
            .where(QuizSession.status == "COMPLETED")
            .where(QuizSession.score_percentage.isnot(None))
        )
        result = await session.execute(stmt)
        avg = result.scalar_one_or_none()
        return float(avg) if avg is not None else None


async def _get_plan_completion_rate(user_id: str) -> float | None:
    """Get overall study plan completion rate (completed_items / total_items)."""
    async with get_session() as session:
        from sqlalchemy import func, select
        from src.domains.personal_learning.db_models import StudyPlan

        stmt = (
            select(
                func.sum(StudyPlan.completed_items),
                func.sum(StudyPlan.total_items),
            )
            .where(StudyPlan.user_id == user_id)
            .where(StudyPlan.total_items > 0)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        if not row or row[1] is None or row[1] == 0:
            return None
        return (row[0] or 0) / row[1] * 100
