"""
Behaviour service — tracks and analyzes learning patterns.

Computes preferred study times, session durations, consistency,
and dropout risk without the learner configuring preferences.
Feeds insights into Home_Service, StudyPlan_Service, and Proactive_Intelligence.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
from typing import Any

from src.shared.time import LearnerTimezone, resolve_learner_timezone, to_learner_local

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


@dataclass(frozen=True)
class PracticeSession:
    """A session, normalised for behaviour analysis.

    Exists so the metric helpers are pure and unit-agnostic about their source.
    The previous shape was a `StudySession` row from the *progress* domain, whose
    `duration` is minutes — but nothing in personal learning has ever written one,
    which is half of why this analysis produced nothing.
    """

    started_at: datetime
    #: `None` when the client never reported one, which is distinct from a
    #: zero-length session. Never inferred from the question count.
    duration_minutes: float | None


#: How far back behaviour is measured. Matches the consistency denominator.
BEHAVIOUR_WINDOW_DAYS = 30

#: Below this, a time-of-day distribution is noise rather than a pattern, and no
#: claim is made from it. Four buckets over a sparse history will routinely leave
#: three empty, so a "you learn best in the morning" from two sessions is an
#: accident of when the learner happened to start.
MIN_SESSIONS_FOR_TIME_PATTERN = 5


async def _load_practice_sessions(user_id: str) -> list[PracticeSession]:
    """Load the learner's recent practice as normalised sessions.

    Reads `QuizSession`, because that is where practice actually happens in this
    domain. The analysis previously expected `StudySession` rows from the progress
    domain, which only `POST /progress/session` writes — so for a learner who
    practises through Prepare there was never any evidence to analyse even if the
    call had been correct.
    """
    since = datetime.now(UTC) - timedelta(days=BEHAVIOUR_WINDOW_DAYS)
    rows = await repo.list_quiz_sessions_since(user_id, since=since)
    return [
        PracticeSession(
            started_at=row.created_at,
            duration_minutes=(
                row.duration_seconds / 60.0
                if row.duration_seconds and row.duration_seconds > 0
                else None
            ),
        )
        for row in rows
        if row.created_at
    ]


async def analyze_behaviour(
    *, user_id: str, sessions: list[PracticeSession] | None = None
) -> dict[str, Any]:
    """
    Compute behaviour metrics for a learner and cache them on their profile.

    Req 11.3: Compute preferred study times, average session duration,
    most productive periods, consistency score (capped at 100), and best day.

    Req 11.6: Identify patterns that correlate with dropout.

    `sessions` is optional and loaded when omitted. That default is the fix for a
    long-standing bug: the argument used to be required, the only caller never
    passed it, and the resulting `TypeError` was swallowed by a bare `except` in
    the daily task — so this function had never once completed and every learner's
    behaviour columns were `NULL`. Making the evidence self-loading means the
    failure cannot recur by omission.

    Time-of-day results are computed in the learner's own timezone. When that is
    unknown the distribution is still recorded, marked `utc_assumed`, so consumers
    can decline to make a claim rather than telling a learner in Lagos they study
    best at an hour that is really someone else's.
    """
    if sessions is None:
        sessions = await _load_practice_sessions(user_id)

    timezone_ = await resolve_learner_timezone(user_id)
    behaviour_data = compute_behaviour(sessions, timezone_)

    await repo.update_profile_behaviour(user_id, behaviour_data)

    # Invalidate the cached behaviour profile since we just updated it
    await _get_behaviour_profile_cached.invalidate(user_id=user_id)

    return behaviour_data


def compute_behaviour(
    sessions: list[PracticeSession], timezone_: LearnerTimezone
) -> dict[str, Any]:
    """The metrics themselves. Pure, so the arithmetic is testable without a DB.

    Nothing measured is reported as zero. With no sessions every field is `None`
    rather than `0.0`, because a learner who has not practised has no average
    session length, and writing `0` there feeds a zero daily budget into study-plan
    generation.
    """
    if not sessions:
        return {
            "preferredStudyTimes": None,
            "avgSessionMinutes": None,
            "consistencyScore": None,
            "bestDayOfWeek": None,
            "dropoutRisk": None,
        }

    local_times = [to_learner_local(s.started_at, timezone_) for s in sessions]

    # Only claimed once there is enough of a spread to be a pattern.
    preferred_times = (
        _compute_preferred_times([t.hour for t in local_times], timezone_, len(sessions))
        if len(sessions) >= MIN_SESSIONS_FOR_TIME_PATTERN
        else None
    )

    durations = [s.duration_minutes for s in sessions if s.duration_minutes]
    avg_duration = round(mean(durations), 1) if durations else None

    # Also a local-time question: a session at 23:30 UTC is Tuesday in London and
    # Wednesday in Lagos, so the "best day" differs by where the learner is.
    best_day = None
    if len(sessions) >= MIN_SESSIONS_FOR_TIME_PATTERN and timezone_.is_known:
        best_day = Counter(t.strftime("%A") for t in local_times).most_common(1)[0][0]

    consistency = _compute_consistency_score(local_times)
    dropout_risk = _compute_dropout_risk(sessions)

    return {
        "preferredStudyTimes": preferred_times,
        "avgSessionMinutes": avg_duration,
        "consistencyScore": min(consistency, 100.0),
        "bestDayOfWeek": best_day,
        "dropoutRisk": round(dropout_risk, 2),
    }


def _compute_preferred_times(
    hours: list[int], timezone_: LearnerTimezone, session_count: int
) -> dict[str, Any]:
    """Cluster start hours into time-of-day buckets, and say what they mean.

    The percentages are nested under `buckets` rather than sitting at the top
    level, so the metadata beside them cannot be mistaken for a bucket. The old
    flat shape made that unsafe: consumers did `max(times, key=times.get)` and
    `sorted(times.items(), key=lambda x: x[1])`, both of which raise `TypeError`
    the moment a non-numeric value shares the dict.

    `basis` is the important field. `local` means the hours are the learner's own
    wall clock and a claim can be made from them; `utc_assumed` means their
    timezone was never captured, the hours are UTC, and the distribution is
    telemetry rather than something to tell them about themselves.
    """
    buckets = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
    for hour in hours:
        if 5 <= hour < 12:
            buckets["morning"] += 1
        elif 12 <= hour < 17:
            buckets["afternoon"] += 1
        elif 17 <= hour < 21:
            buckets["evening"] += 1
        else:
            buckets["night"] += 1

    total = sum(buckets.values()) or 1
    return {
        "buckets": {name: round(count / total * 100, 1) for name, count in buckets.items()},
        "basis": "local" if timezone_.is_known else "utc_assumed",
        "timezone": timezone_.name if timezone_.is_known else None,
        "sessionCount": session_count,
    }


def _compute_consistency_score(local_times: list[datetime]) -> float:
    """Ratio of days practised to days in the period. Every day = 100.

    Takes times already converted to the learner's wall clock, because "how many
    distinct days" is a question about their calendar: a session at 23:30 in Lagos
    is the next day in UTC, so counting UTC dates can either merge two of the
    learner's days into one or split one across two.

    Still applies its own window even though the loader already does. Consistency
    is defined over a period, so a caller handing in older sessions should not get
    a score that silently describes a different period than the one named.
    """
    if not local_times:
        return 0.0

    cutoff = datetime.now(UTC) - timedelta(days=BEHAVIOUR_WINDOW_DAYS)
    in_window = [t for t in local_times if t >= cutoff]
    if not in_window:
        return 0.0

    study_dates = {t.date() for t in in_window}

    # Measured from the learner's first session in the window rather than the full
    # window, so someone who joined four days ago is not scored as having missed
    # twenty-six days they could not have practised on.
    latest = max(in_window).date()
    days_in_period = min(BEHAVIOUR_WINDOW_DAYS, (latest - min(study_dates)).days + 1)
    if days_in_period <= 0:
        return 0.0

    return min((len(study_dates) / days_in_period) * 100, 100.0)


def _compute_dropout_risk(sessions: list[PracticeSession]) -> float:
    """
    Detect dropout risk based on:
    - Declining session duration
    - Growing gaps between sessions
    Returns a score from 0 (no risk) to 1 (high risk).

    Timezone-independent: gaps and durations are elapsed time, and "three days
    since the last session" is the same interval wherever the learner is.
    """
    if len(sessions) < 3:
        return 0.0

    sorted_sessions = sorted(sessions, key=lambda s: s.started_at)

    # Check for declining durations (last 5 sessions)
    recent = sorted_sessions[-5:]
    durations = [s.duration_minutes for s in recent if s.duration_minutes]
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
            sorted_sessions[i].started_at - sorted_sessions[i - 1].started_at
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
    now = datetime.now(UTC)
    days_since_last = (now - sorted_sessions[-1].started_at).days
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


def _local_buckets(preferred_times: Any) -> dict[str, float] | None:
    """The bucket percentages, but only when they mean the learner's own clock.

    Returns `None` for an assumed basis. That refusal is the point: without a
    captured timezone the hours are UTC, so any slot named from them is the wrong
    slot for most of the world, and a confident sentence about when someone studies
    best is worse than saying nothing.

    Tolerates the pre-nesting shape by ignoring it — an old cached value has no
    basis recorded, so it cannot be shown to be local.
    """
    if not isinstance(preferred_times, dict):
        return None
    if preferred_times.get("basis") != "local":
        return None
    buckets = preferred_times.get("buckets")
    if not isinstance(buckets, dict) or not buckets:
        return None
    return {name: value for name, value in buckets.items() if isinstance(value, int | float)}


def _compute_predictive_scheduling(profile_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Compute predictive scheduling suggestions based on behaviour patterns.

    PLUS-only feature: predicts optimal study schedule for the upcoming week.

    Returns `None` when there is not enough to say — no captured timezone, no
    pattern yet, or no measured session length — rather than defaulting to
    "morning" and 45 minutes, which the previous version did and which described a
    learner nobody had observed.
    """
    buckets = _local_buckets(profile_data.get("preferredTimes"))
    consistency = profile_data.get("consistencyScore")
    avg_minutes = profile_data.get("avgSessionMinutes")

    if not buckets or not avg_minutes:
        return None

    best_slot = max(buckets, key=lambda name: buckets[name])

    return {
        "suggestedSlot": best_slot,
        # Stretched slightly beyond what they have sustained, not invented.
        "suggestedDurationMinutes": min(int(avg_minutes * 1.1), 90),
        "consistencyTrend": "improving" if (consistency or 0) > 60 else "building",
        "weeklyGoalSessions": 5 if (consistency or 0) > 70 else 3,
    }


def _compute_optimal_times(profile_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Compute optimal study time suggestions.

    PLUS-only feature: identifies when the learner practises most.

    Note what this measures. It is a *frequency* claim — where practice volume
    falls — and the copy says so. When the learner performs best is a different
    question, answered by `prep_productive_time` from accuracy per hour, and the
    two should not be conflated in what a learner is told.
    """
    buckets = _local_buckets(profile_data.get("preferredTimes"))
    if not buckets:
        return None

    ranked = sorted(buckets.items(), key=lambda item: item[1], reverse=True)
    primary, primary_pct = ranked[0]

    return {
        "primarySlot": primary,
        "primaryPercentage": primary_pct,
        "secondarySlot": ranked[1][0] if len(ranked) > 1 else None,
        "recommendation": (
            f"Most of your practice happens in the {primary} — "
            f"{primary_pct:.0f}% of your sessions."
        ),
    }
