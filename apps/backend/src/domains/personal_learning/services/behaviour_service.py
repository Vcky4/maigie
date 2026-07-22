"""
Behaviour service — tracks and analyzes learning patterns.

Computes preferred study times, session durations, consistency,
and dropout risk without the learner configuring preferences.
Feeds insights into Home_Service, StudyPlan_Service, and Proactive_Intelligence.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from ..repository import personal_learning_repo as repo
from .cache import cached

logger = logging.getLogger(__name__)


async def get_behaviour_profile(*, user_id: str) -> dict[str, Any]:
    """
    Return the learner's cached behaviour profile from LearningProfile.

    Req 11.4: Return preferred_times, avg_session_minutes, consistency_score,
    best_day_of_week, and dropout_risk_factors.

    FREE: Basic profile (preferred times, avg session, consistency, best day).
    PLUS: Full profile + predictive scheduling, optimal time suggestions, dropout prevention.

    Cached for 120s — behaviour data changes only via background task (daily).
    """
    from . import feature_tier_service

    profile_data = await _get_behaviour_profile_cached(user_id=user_id)

    # Check tier for predictive features
    quality_tier = await feature_tier_service.get_quality_tier(user_id)

    if quality_tier == "plus":
        # PLUS: include predictive features
        from . import trial_service
        await trial_service.record_plus_feature_used(user_id, "behaviour_analytics")
        profile_data["predictiveScheduling"] = _compute_predictive_scheduling(profile_data)
        profile_data["optimalStudyTimes"] = _compute_optimal_times(profile_data)
        profile_data["tier"] = "plus"
    else:
        # FREE: basic profile only — indicate locked features
        profile_data["predictiveScheduling"] = None
        profile_data["optimalStudyTimes"] = None
        profile_data["tier"] = "free"
        profile_data["lockedFeatures"] = [
            "predictive_scheduling",
            "optimal_time_suggestions",
            "dropout_prevention",
        ]

    return profile_data


@cached(ttl_seconds=120, max_size=1000, key_arg="user_id")
async def _get_behaviour_profile_cached(*, user_id: str) -> dict[str, Any]:
    """Cached inner implementation."""
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return {
            "preferredTimes": None,
            "avgSessionMinutes": None,
            "consistencyScore": None,
            "bestDayOfWeek": None,
            "dropoutRiskFactors": None,
        }
    return {
        "preferredTimes": profile.preferred_study_times,
        "avgSessionMinutes": profile.avg_session_minutes,
        "consistencyScore": profile.consistency_score,
        "bestDayOfWeek": profile.best_day_of_week,
        "dropoutRiskFactors": _compute_risk_factors(profile.dropout_risk),
    }


async def analyze_behaviour(*, user_id: str, sessions: list[Any]) -> dict[str, Any]:
    """
    Compute behaviour metrics from a list of study sessions.
    Called by the background task with recent session data.

    Req 11.3: Compute preferred study times, average session duration,
    most productive periods, consistency score (capped at 100), and best day.

    Req 11.6: Identify patterns that correlate with dropout.
    """
    if not sessions:
        return {
            "preferredStudyTimes": None,
            "avgSessionMinutes": 0.0,
            "consistencyScore": 0.0,
            "bestDayOfWeek": None,
            "dropoutRisk": 0.0,
        }

    # Compute preferred study times (cluster start hours)
    hours = [s.start_time.hour for s in sessions if s.start_time]
    preferred_times = _compute_preferred_times(hours)

    # Average session duration
    durations = [s.duration for s in sessions if s.duration and s.duration > 0]
    avg_duration = mean(durations) if durations else 0.0

    # Best day of week
    days = [s.start_time.strftime("%A") for s in sessions if s.start_time]
    day_counter = Counter(days)
    best_day = day_counter.most_common(1)[0][0] if day_counter else None

    # Consistency score (how regularly the learner studies, capped at 100)
    consistency = _compute_consistency_score(sessions)

    # Dropout risk
    dropout_risk = _compute_dropout_risk(sessions)

    # Update the profile cache
    behaviour_data = {
        "preferredStudyTimes": preferred_times,
        "avgSessionMinutes": round(avg_duration, 1),
        "consistencyScore": min(consistency, 100.0),  # Capped at 100
        "bestDayOfWeek": best_day,
        "dropoutRisk": round(dropout_risk, 2),
    }
    await repo.update_profile_behaviour(user_id, behaviour_data)

    # Invalidate the cached behaviour profile since we just updated it
    await _get_behaviour_profile_cached.invalidate(user_id=user_id)

    return behaviour_data


def _compute_preferred_times(hours: list[int]) -> dict[str, Any]:
    """Cluster start times into time-of-day buckets."""
    buckets = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
    for h in hours:
        if 5 <= h < 12:
            buckets["morning"] += 1
        elif 12 <= h < 17:
            buckets["afternoon"] += 1
        elif 17 <= h < 21:
            buckets["evening"] += 1
        else:
            buckets["night"] += 1

    total = sum(buckets.values()) or 1
    return {k: round(v / total * 100, 1) for k, v in buckets.items()}


def _compute_consistency_score(sessions: list[Any]) -> float:
    """
    Compute consistency: ratio of active days to total days in period.
    A learner who studies every day = 100. Sporadic = low score.
    """
    if not sessions:
        return 0.0

    # Get unique study dates in the last 30 days
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    study_dates = set()
    for s in sessions:
        if s.start_time and s.start_time >= thirty_days_ago:
            study_dates.add(s.start_time.date())

    # Calculate days in period (max 30)
    if not study_dates:
        return 0.0

    days_in_period = min(30, (now.date() - min(study_dates)).days + 1)
    if days_in_period <= 0:
        return 0.0

    score = (len(study_dates) / days_in_period) * 100
    return min(score, 100.0)  # Cap at 100


def _compute_dropout_risk(sessions: list[Any]) -> float:
    """
    Detect dropout risk based on:
    - Declining session duration
    - Growing gaps between sessions
    Returns a score from 0 (no risk) to 1 (high risk).
    """
    if len(sessions) < 3:
        return 0.0

    # Sort by start_time
    sorted_sessions = sorted(sessions, key=lambda s: s.start_time)

    # Check for declining durations (last 5 sessions)
    recent = sorted_sessions[-5:]
    durations = [s.duration for s in recent if s.duration]
    duration_declining = False
    if len(durations) >= 3:
        # Compare first half avg to second half avg
        mid = len(durations) // 2
        first_half = mean(durations[:mid]) if durations[:mid] else 0
        second_half = mean(durations[mid:]) if durations[mid:] else 0
        if first_half > 0 and second_half < first_half * 0.7:
            duration_declining = True

    # Check for growing gaps
    gaps = []
    for i in range(1, len(sorted_sessions)):
        gap = (
            sorted_sessions[i].start_time - sorted_sessions[i - 1].start_time
        ).total_seconds() / 3600
        gaps.append(gap)

    gap_growing = False
    if len(gaps) >= 3:
        recent_gaps = gaps[-3:]
        earlier_gaps = gaps[:-3] if len(gaps) > 3 else gaps[:1]
        if earlier_gaps and mean(recent_gaps) > mean(earlier_gaps) * 2:
            gap_growing = True

    # Risk score
    risk = 0.0
    if duration_declining:
        risk += 0.4
    if gap_growing:
        risk += 0.4

    # Also check if no sessions in last 3 days
    now = datetime.now(timezone.utc)
    last_session = sorted_sessions[-1]
    days_since_last = (now - last_session.start_time).days
    if days_since_last >= 3:
        risk += 0.2

    return min(risk, 1.0)


def _compute_risk_factors(dropout_risk: float | None) -> list[str] | None:
    """Convert dropout risk score to human-readable factors."""
    if dropout_risk is None or dropout_risk == 0:
        return None

    factors = []
    if dropout_risk >= 0.4:
        factors.append("declining_session_duration")
    if dropout_risk >= 0.6:
        factors.append("growing_gaps_between_sessions")
    if dropout_risk >= 0.8:
        factors.append("extended_inactivity")

    return factors if factors else None


def _compute_predictive_scheduling(profile_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Compute predictive scheduling suggestions based on behaviour patterns.

    PLUS-only feature: predicts optimal study schedule for the upcoming week.
    """
    preferred_times = profile_data.get("preferredTimes")
    consistency = profile_data.get("consistencyScore")
    avg_minutes = profile_data.get("avgSessionMinutes")

    if not preferred_times or not avg_minutes:
        return None

    # Find the best time slot
    best_slot = max(preferred_times, key=preferred_times.get) if preferred_times else "morning"
    suggested_duration = int(avg_minutes * 1.1) if avg_minutes else 45  # Slightly stretch

    return {
        "suggestedSlot": best_slot,
        "suggestedDurationMinutes": min(suggested_duration, 90),
        "consistencyTrend": "improving" if (consistency or 0) > 60 else "building",
        "weeklyGoalSessions": 5 if (consistency or 0) > 70 else 3,
    }


def _compute_optimal_times(profile_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Compute optimal study time suggestions.

    PLUS-only feature: identifies when the learner performs best.
    """
    preferred_times = profile_data.get("preferredTimes")
    if not preferred_times:
        return None

    # Rank time slots by usage
    sorted_slots = sorted(preferred_times.items(), key=lambda x: x[1], reverse=True)

    return {
        "primarySlot": sorted_slots[0][0] if sorted_slots else "morning",
        "primaryPercentage": sorted_slots[0][1] if sorted_slots else 0,
        "secondarySlot": sorted_slots[1][0] if len(sorted_slots) > 1 else None,
        "recommendation": (
            f"You learn best in the {sorted_slots[0][0]} — "
            f"{sorted_slots[0][1]:.0f}% of your study sessions happen then."
            if sorted_slots
            else "Keep studying to build enough data for personalised time suggestions."
        ),
    }
