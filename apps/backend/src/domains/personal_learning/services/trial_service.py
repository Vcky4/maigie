"""
Trial Service — Manages the PLUS trial lifecycle (TRIAL_DURATION_DAYS).

Allows free users to experience PLUS capabilities temporarily.
Handles start, status checks, showcase suggestions, summary generation,
and graceful expiry (no data loss).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.database.session import get_session_factory

logger = logging.getLogger(__name__)

# ===========================================================================
# Constants
# ===========================================================================

# The second copy of `config.TRIAL_DAYS_MAIGIE_PLUS`, and the one that governs the
# in-product trial rather than the Stripe subscription's own trial period. Both read 3.
#
# Three days rather than seven because a free 7-day trial sitting beside a $2.49 7-day
# pass is the same product at two prices, and the one that costs money looks like a trick
# to anyone who remembers the free one. Three days separates them: the trial is a look,
# the pass is a study week. At a 5-hour usage window that is still ~14 windows, several
# study sessions, and every Plus capability — long enough to be an honest look.
TRIAL_DURATION_DAYS = 3
TRIAL_COOLDOWN_DAYS = 90  # Can only trial once per quarter


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

    @property
    def trial_available(self) -> bool:
        """Whether starting a trial right now would succeed.

        Derived here so it cannot disagree with `start_trial`'s own rules, and so it
        is answered on every branch. It previously appeared **only** in the route's
        "no status at all" fallback: a learner whose trial had expired and whose
        cooldown had since elapsed got a response with no `trialAvailable`
        key at all. The client read `undefined`, treated it as false, and hid the
        offer from someone who was eligible — while the paywall that sent them there
        had just told them a trial existed.
        """
        if self.is_active:
            return False
        # Inside the cooldown window `next_trial_available_at` is set; once it has
        # passed, `get_trial_status` leaves it None and the learner is eligible again.
        return self.next_trial_available_at is None


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
    Start a PLUS trial for a free user, TRIAL_DURATION_DAYS long.

    Raises ValueError if:
    - User is already on trial
    - User has trialed within the last TRIAL_COOLDOWN_DAYS days
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

    now = datetime.now(UTC)

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

    now = datetime.now(UTC)

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

    Deeply personalised — references specific content the user has:
    - Weak quiz topics → suggest adaptive quiz on that specific topic
    - Specific notes → suggest advanced flashcards from that note
    - Active prep with deadline → suggest adaptive study plan for it
    - Recent activity → suggest deep reflection on those patterns
    """
    from sqlalchemy import func, select

    from src.domains.personal_learning.db_models import (
        ExamPrep,
        Note,
        PrepTopic,
        QuizSession,
        StudyPlan,
    )
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    used_features = set(profile.plus_features_used_this_period or []) if profile else set()
    suggestions: list[ShowcaseSuggestion] = []

    factory = get_session_factory()

    # --- 1. Adaptive Quiz: find weak topics from recent quizzes ---
    if "quiz_modes" not in used_features:
        async with factory() as session:
            # Find topics where mastery < 70% in user's preps
            stmt = (
                select(PrepTopic.title, PrepTopic.mastery_score, PrepTopic.prep_id)
                .join(ExamPrep, ExamPrep.id == PrepTopic.prep_id)
                .where(ExamPrep.user_id == user_id)
                .where(PrepTopic.mastery_score < 70.0)
                .where(PrepTopic.mastery_score > 0)  # Has been attempted
                .order_by(PrepTopic.mastery_score.asc())
                .limit(3)
            )
            result = await session.execute(stmt)
            weak_topics = result.all()

        if weak_topics:
            topic_name = weak_topics[0][0]
            score = int(weak_topics[0][1])
            prep_id = weak_topics[0][2]
            weak_names = [t[0] for t in weak_topics[:3]]
            suggestions.append(
                ShowcaseSuggestion(
                    capability_id="quiz_modes",
                    title=f"Adaptive quiz: {topic_name}",
                    description=(
                        f"You scored {score}% on '{topic_name}'. "
                        f"Adaptive mode focuses questions on your weak areas until you master them."
                    ),
                    action_url=f"/learning/preparations/{prep_id}/quizzes?mode=ADAPTIVE",
                    reason=f"Weak areas detected: {', '.join(weak_names)}",
                )
            )
        else:
            # No weak topics yet — check if they have preps at all
            async with factory() as session:
                stmt = (
                    select(ExamPrep.id, ExamPrep.subject)
                    .where(ExamPrep.user_id == user_id)
                    .where(ExamPrep.status != "COMPLETED")
                    .limit(1)
                )
                result = await session.execute(stmt)
                active_prep = result.first()

            if active_prep:
                suggestions.append(
                    ShowcaseSuggestion(
                        capability_id="quiz_modes",
                        title=f"Past paper simulation: {active_prep[1]}",
                        description=(
                            f"Simulate exam conditions for '{active_prep[1]}' — "
                            f"timed questions in exam format to build confidence before the real thing."
                        ),
                        action_url=f"/learning/preparations/{active_prep[0]}/quizzes?mode=PAST_PAPER_SIM",
                        reason="Practice under exam conditions to reduce test anxiety",
                    )
                )

    # --- 2. Advanced Flashcards: find a specific note with content ---
    if "flashcard_generation" not in used_features:
        async with factory() as session:
            stmt = (
                select(Note.id, Note.title)
                .where(Note.user_id == user_id)
                .where(Note.content.isnot(None))
                .where(Note.archived.is_(False))
                .order_by(Note.updated_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            recent_note = result.first()

        if recent_note:
            suggestions.append(
                ShowcaseSuggestion(
                    capability_id="flashcard_generation",
                    title=f"Advanced flashcards from '{recent_note[1]}'",
                    description=(
                        "Generate cloze deletions and multiple-choice cards from your note — "
                        "varied question types improve retention by 40% vs basic Q&A."
                    ),
                    action_url=f"/learning/flashcards/generate/note/{recent_note[0]}",
                    reason=f"Your note '{recent_note[1]}' has content ideal for advanced cards",
                )
            )

    # --- 3. Adaptive Study Plan: find a prep with deadline ---
    if "study_plan" not in used_features:
        async with factory() as session:
            stmt = (
                select(ExamPrep.id, ExamPrep.subject, ExamPrep.exam_date)
                .where(ExamPrep.user_id == user_id)
                .where(ExamPrep.status != "COMPLETED")
                .order_by(ExamPrep.exam_date.asc())
                .limit(1)
            )
            result = await session.execute(stmt)
            next_prep = result.first()

        if next_prep:
            exam_date = next_prep[2]
            if exam_date:
                # Ensure timezone-aware for comparison
                if exam_date.tzinfo is None:
                    exam_date = exam_date.replace(tzinfo=UTC)
                days_until = (exam_date - datetime.now(UTC)).days
            else:
                days_until = None
            suggestions.append(
                ShowcaseSuggestion(
                    capability_id="study_plan",
                    title=f"Adaptive plan for {next_prep[1]}",
                    description=(
                        f"{'Only ' + str(days_until) + ' days until your exam. ' if days_until and days_until < 60 else ''}"
                        f"An adaptive plan adjusts daily based on your quiz performance — "
                        f"spending more time on weak topics and less on what you've already mastered."
                    ),
                    action_url=f"/learning/study-plans?prepId={next_prep[0]}",
                    reason=f"Adaptive scheduling for '{next_prep[1]}' based on your actual performance",
                )
            )

    # --- 4. Deep Reflection: if user has enough activity ---
    if "reflection" not in used_features and (profile and (profile.maturity_days or 0) >= 5):
        async with factory() as session:
            # Count recent activities
            from datetime import timedelta

            from src.domains.personal_learning.db_models import ActivityFeedEntry

            week_ago = datetime.now(UTC) - timedelta(days=7)
            stmt = (
                select(func.count())
                .select_from(ActivityFeedEntry)
                .where(ActivityFeedEntry.user_id == user_id)
                .where(ActivityFeedEntry.occurred_at >= week_ago)
            )
            result = await session.execute(stmt)
            activity_count = result.scalar_one() or 0

        if activity_count >= 3:
            suggestions.append(
                ShowcaseSuggestion(
                    capability_id="reflection",
                    title="Deep analysis of your learning patterns",
                    description=(
                        f"You've had {activity_count} learning activities this week. "
                        f"A deep reflection can identify which topics reinforce each other "
                        f"and predict what to focus on next."
                    ),
                    action_url="/learning/reflections/generate?type=weekly",
                    reason="Enough activity to identify meaningful cross-topic patterns",
                )
            )

    # --- 5. Document Generation: suggest based on notes/preps ---
    if "document_generation" not in used_features and len(suggestions) < 3:
        async with factory() as session:
            stmt = (
                select(Note.id, Note.title)
                .where(Note.user_id == user_id)
                .where(Note.content.isnot(None))
                .order_by(Note.updated_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            note_for_doc = result.first()

        if note_for_doc:
            suggestions.append(
                ShowcaseSuggestion(
                    capability_id="document_generation",
                    title=f"Turn '{note_for_doc[1]}' into a presentation",
                    description=(
                        "Generate a polished PPTX from your note content — "
                        "great for study groups or revision summaries."
                    ),
                    action_url=f"/learning/documents?type=presentation&noteId={note_for_doc[0]}",
                    reason="Your note has content ready to become a shareable presentation",
                )
            )

    return suggestions[:3]


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
    losses = [loss for loss in losses if loss]

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
    now = datetime.now(UTC)

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
