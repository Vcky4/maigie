"""
Retention Service — Churn risk detection and value-based retention.

Identifies subscribers at risk of churning and takes proactive value-based
actions. Never uses dark patterns or guilt — only factual value communication.

Book principle: "Renewal should be earned through value. Never through habit."
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

CHURN_RISK_THRESHOLD = 0.7  # Score above this triggers intervention
INTERVENTION_COOLDOWN_DAYS = 7  # Min days between interventions
WINBACK_WINDOW_DAYS = 90  # Offer winback within N days of churn
WINBACK_DISCOUNT_PERCENT = 30

# Churn risk signal weights (sum to 1.0)
SIGNAL_WEIGHTS = {
    "login_frequency_decline": 0.30,
    "feature_usage_decline": 0.25,
    "plus_feature_absence": 0.20,
    "behaviour_dropout_risk": 0.15,
    "days_since_activity": 0.10,
}


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class ChurnRiskProfile:
    """Detailed churn risk assessment."""

    score: float  # 0.0 - 1.0
    signals: dict[str, float]  # Individual signal scores
    primary_risk_factor: str
    recommendation: str


@dataclass
class RetentionAction:
    """A retention intervention to deliver."""

    intervention_type: str  # "feature_reminder", "pause_offer", "value_summary"
    message: str
    action_url: str | None = None
    feature_suggestion: str | None = None


@dataclass
class PauseOffer:
    """Subscription pause offer details."""

    duration_days: int = 30
    message: str = ""
    data_preserved: bool = True
    resume_url: str = "/subscription/resume"


@dataclass
class WinbackOffer:
    """Welcome-back offer for recently churned users."""

    discount_percent: int = WINBACK_DISCOUNT_PERCENT
    message: str = ""
    offer_url: str = "/subscription/reactivate"
    expires_at: datetime | None = None
    learning_context_intact: bool = True


# ===========================================================================
# Service Functions
# ===========================================================================


async def calculate_churn_risk(user_id: str) -> ChurnRiskProfile:
    """
    Calculate churn risk score for a PLUS subscriber.

    Score is 0.0 (no risk) to 1.0 (very high risk).
    Uses weighted signals based on behavioural patterns.
    """
    signals: dict[str, float] = {}

    # Signal 1: Login frequency decline (0.0 = stable/growing, 1.0 = severe decline)
    signals["login_frequency_decline"] = await _calc_login_decline(user_id)

    # Signal 2: Feature usage decline vs previous period
    signals["feature_usage_decline"] = await _calc_feature_decline(user_id)

    # Signal 3: PLUS feature absence in current period
    signals["plus_feature_absence"] = await _calc_plus_feature_absence(user_id)

    # Signal 4: Behaviour service dropout risk
    signals["behaviour_dropout_risk"] = await _calc_behaviour_dropout(user_id)

    # Signal 5: Days since last activity
    signals["days_since_activity"] = await _calc_inactivity_signal(user_id)

    # Calculate weighted score
    score = sum(
        signals.get(signal, 0.0) * weight
        for signal, weight in SIGNAL_WEIGHTS.items()
    )
    score = max(0.0, min(1.0, score))  # Clamp to [0, 1]

    # Identify primary risk factor
    primary_factor = max(signals, key=lambda k: signals[k] * SIGNAL_WEIGHTS[k])

    # Generate recommendation
    recommendation = _generate_recommendation(primary_factor, signals[primary_factor])

    return ChurnRiskProfile(
        score=score,
        signals=signals,
        primary_risk_factor=primary_factor,
        recommendation=recommendation,
    )


async def evaluate_retention_intervention(user_id: str) -> RetentionAction | None:
    """
    Determine if a retention intervention is needed.

    Returns None if no intervention appropriate (risk too low or cooldown active).
    """
    # Calculate risk
    risk_profile = await calculate_churn_risk(user_id)

    if risk_profile.score < CHURN_RISK_THRESHOLD:
        return None

    # Check cooldown
    if await _intervention_recently_delivered(user_id):
        return None

    # Select intervention type based on primary risk factor
    intervention = _select_intervention(risk_profile)

    # Record the intervention
    await _record_intervention(user_id, risk_profile.score, intervention.intervention_type)

    logger.info(
        f"Retention intervention for user {user_id}: "
        f"type={intervention.intervention_type}, risk={risk_profile.score:.2f}"
    )

    return intervention


async def offer_pause(user_id: str) -> PauseOffer:
    """
    Generate a subscription pause offer.

    The pause option is always genuine — 30 days pause with all data preserved.
    """
    return PauseOffer(
        duration_days=30,
        message=(
            "Need a break? You can pause your subscription for 30 days. "
            "Your notes, flashcards, study plans, and all learning data remain "
            "exactly as you left them. Resume anytime."
        ),
        data_preserved=True,
    )


async def generate_winback_offer(user_id: str) -> WinbackOffer | None:
    """
    Generate a welcome-back offer for users who churned within 90 days.

    Returns None if user churned too long ago or never churned.
    """
    # Check when user's tier changed to FREE (approximate churn date)
    churn_date = await _get_approximate_churn_date(user_id)
    if not churn_date:
        return None

    now = datetime.now(timezone.utc)
    days_since_churn = (now - churn_date).days

    if days_since_churn > WINBACK_WINDOW_DAYS:
        return None

    expires_at = churn_date + timedelta(days=WINBACK_WINDOW_DAYS)

    return WinbackOffer(
        discount_percent=WINBACK_DISCOUNT_PERCENT,
        message=(
            f"Welcome back! Your learning context is exactly as you left it. "
            f"Get {WINBACK_DISCOUNT_PERCENT}% off your first month back — "
            f"pick up where you left off with enhanced AI capabilities."
        ),
        offer_url="/subscription/reactivate",
        expires_at=expires_at,
        learning_context_intact=True,
    )


async def record_intervention_outcome(
    intervention_id: str, outcome: str
) -> None:
    """
    Record the outcome of a retention intervention.

    Outcomes: "retained", "churned", "paused"
    """
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.domains.personal_learning.db_models import RetentionIntervention

        stmt = select(RetentionIntervention).where(RetentionIntervention.id == intervention_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.outcome = outcome
            record.outcome_at = datetime.now(timezone.utc)
            await session.commit()


# ===========================================================================
# Internal Helpers
# ===========================================================================


async def _calc_login_decline(user_id: str) -> float:
    """
    Calculate login frequency decline signal.

    Compares last 14 days vs previous 14 days.
    Returns 0.0 (stable/growing) to 1.0 (severe decline).
    """
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import func, select
        from src.domains.personal_learning.db_models import ActivityFeedEntry

        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(days=14)
        previous_start = now - timedelta(days=28)

        # Recent activity count
        recent_stmt = (
            select(func.count())
            .select_from(ActivityFeedEntry)
            .where(ActivityFeedEntry.user_id == user_id)
            .where(ActivityFeedEntry.occurred_at >= recent_start)
        )
        recent_result = await session.execute(recent_stmt)
        recent_count = recent_result.scalar_one() or 0

        # Previous period count
        prev_stmt = (
            select(func.count())
            .select_from(ActivityFeedEntry)
            .where(ActivityFeedEntry.user_id == user_id)
            .where(ActivityFeedEntry.occurred_at >= previous_start)
            .where(ActivityFeedEntry.occurred_at < recent_start)
        )
        prev_result = await session.execute(prev_stmt)
        prev_count = prev_result.scalar_one() or 0

    if prev_count == 0:
        return 0.0 if recent_count > 0 else 0.5

    decline_ratio = 1.0 - (recent_count / prev_count)
    return max(0.0, min(1.0, decline_ratio))


async def _calc_feature_decline(user_id: str) -> float:
    """Calculate feature usage decline vs previous period."""
    # Simplified: compare document + quiz counts between periods
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import func, select
        from src.domains.personal_learning.db_models import GeneratedDocument, QuizSession

        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(days=14)
        previous_start = now - timedelta(days=28)

        # Recent docs
        recent_docs = (
            select(func.count())
            .select_from(GeneratedDocument)
            .where(GeneratedDocument.user_id == user_id)
            .where(GeneratedDocument.created_at >= recent_start)
        )
        r1 = await session.execute(recent_docs)
        recent = r1.scalar_one() or 0

        # Previous docs
        prev_docs = (
            select(func.count())
            .select_from(GeneratedDocument)
            .where(GeneratedDocument.user_id == user_id)
            .where(GeneratedDocument.created_at >= previous_start)
            .where(GeneratedDocument.created_at < recent_start)
        )
        r2 = await session.execute(prev_docs)
        previous = r2.scalar_one() or 0

    if previous == 0:
        return 0.0 if recent > 0 else 0.3

    decline = 1.0 - (recent / previous)
    return max(0.0, min(1.0, decline))


async def _calc_plus_feature_absence(user_id: str) -> float:
    """Check if user is not using any PLUS-exclusive features this period."""
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile:
        return 0.5

    used = profile.plus_features_used_this_period or []
    if len(used) == 0:
        return 1.0  # Not using any PLUS features
    elif len(used) <= 1:
        return 0.5  # Barely using PLUS features
    else:
        return 0.0  # Actively using PLUS features


async def _calc_behaviour_dropout(user_id: str) -> float:
    """Get dropout risk from the behaviour service."""
    from src.domains.personal_learning.repository import PersonalLearningRepository

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)

    if not profile or profile.dropout_risk is None:
        return 0.3  # Default moderate risk when unknown

    return max(0.0, min(1.0, profile.dropout_risk))


async def _calc_inactivity_signal(user_id: str) -> float:
    """Calculate signal based on days since last activity."""
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import func, select
        from src.domains.personal_learning.db_models import ActivityFeedEntry

        stmt = (
            select(func.max(ActivityFeedEntry.occurred_at))
            .where(ActivityFeedEntry.user_id == user_id)
        )
        result = await session.execute(stmt)
        last_activity = result.scalar_one_or_none()

    if not last_activity:
        return 0.8

    days_inactive = (datetime.now(timezone.utc) - last_activity).days

    if days_inactive <= 2:
        return 0.0
    elif days_inactive <= 5:
        return 0.2
    elif days_inactive <= 10:
        return 0.5
    elif days_inactive <= 14:
        return 0.7
    else:
        return 1.0


async def _intervention_recently_delivered(user_id: str) -> bool:
    """Check if an intervention was delivered within the cooldown period."""
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.domains.personal_learning.db_models import RetentionIntervention

        cutoff = datetime.now(timezone.utc) - timedelta(days=INTERVENTION_COOLDOWN_DAYS)
        stmt = (
            select(RetentionIntervention.id)
            .where(RetentionIntervention.user_id == user_id)
            .where(RetentionIntervention.delivered_at >= cutoff)
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


def _select_intervention(risk_profile: ChurnRiskProfile) -> RetentionAction:
    """Select the appropriate intervention type based on risk signals."""
    primary = risk_profile.primary_risk_factor

    if primary == "plus_feature_absence":
        return RetentionAction(
            intervention_type="feature_reminder",
            message=(
                "You haven't used some of your Plus capabilities recently. "
                "Adaptive quizzes could help with your current preparations."
            ),
            action_url="/learning/preparations",
            feature_suggestion="quiz_modes",
        )
    elif primary in ("login_frequency_decline", "days_since_activity"):
        return RetentionAction(
            intervention_type="value_summary",
            message=(
                "We noticed you've been less active. Here's what Plus has helped you "
                "achieve so far — and what's waiting when you're ready to continue."
            ),
            action_url="/learning/value-summary",
        )
    else:
        return RetentionAction(
            intervention_type="pause_offer",
            message=(
                "Need a break? You can pause your subscription for 30 days. "
                "All your learning data stays exactly as it is."
            ),
            action_url="/subscription/pause",
        )


def _generate_recommendation(primary_factor: str, signal_value: float) -> str:
    """Generate a human-readable recommendation based on risk signals."""
    recommendations = {
        "login_frequency_decline": "Consider sending a value summary or re-engagement notification",
        "feature_usage_decline": "Suggest underutilised features aligned with user's goals",
        "plus_feature_absence": "Remind user about Plus capabilities they haven't explored",
        "behaviour_dropout_risk": "Proactive support — offer study schedule adjustment",
        "days_since_activity": "Gentle re-engagement with progress snapshot",
    }
    return recommendations.get(primary_factor, "Monitor and reassess in next cycle")


async def _record_intervention(
    user_id: str, churn_risk_score: float, intervention_type: str
) -> None:
    """Record a delivered intervention."""
    factory = get_session_factory()
    async with factory() as session:
        from src.domains.personal_learning.db_models import RetentionIntervention

        record = RetentionIntervention(
            id=__import__("uuid").uuid4().hex[:25],
            user_id=user_id,
            churn_risk_score=churn_risk_score,
            intervention_type=intervention_type,
            delivered_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.commit()


async def _get_approximate_churn_date(user_id: str) -> datetime | None:
    """Get approximate date when user churned (tier changed to FREE from PREMIUM)."""
    # This would ideally check audit logs or subscription events.
    # Simplified: check if user is FREE with a last_trial_ended_at or similar signal.
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        from src.domains.identity.db_models import User

        stmt = select(User.tier, User.updated_at).where(User.id == user_id)
        result = await session.execute(stmt)
        row = result.one_or_none()

    if not row:
        return None

    tier = str(row[0]) if row[0] else "FREE"
    if tier != "FREE":
        return None  # User is still subscribed

    # Use updated_at as approximate churn date
    return row[1]
