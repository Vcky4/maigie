"""What a learner's stated pace and confidence actually mean.

The create wizard offers three paces, each labelled with an effort level ("3
sessions each week", "About 2 hours weekly"). That mapping used to exist only as
copy in the client, alongside a separate `paceSessions` constant, so the label a
learner read and the schedule they got were defined in two places and neither was
the server. This module is the single definition.

Confidence is deliberately *not* turned into a mastery score. It is a
self-report, and seeding measured mastery from it would make readiness partly
fictional — the same objection that keeps invented data out of the contract
everywhere else. It is used only to choose where a learner starts, never to claim
they already know something.
"""

from __future__ import annotations

from typing import Literal

PreparationPace = Literal["LIGHT", "BALANCED", "INTENSIVE"]
PreparationConfidence = Literal["STARTING", "DEVELOPING", "CONFIDENT"]

# Weekly effort per pace, matching the wizard's own copy so the learner gets the
# schedule they were shown.
_PACE_SESSIONS_PER_WEEK: dict[str, int] = {
    "LIGHT": 3,
    "BALANCED": 5,
    "INTENSIVE": 7,
}

_PACE_WEEKLY_MINUTES: dict[str, int] = {
    "LIGHT": 120,  # "About 2 hours weekly"
    "BALANCED": 240,  # "About 4 hours weekly"
    "INTENSIVE": 360,  # "6+ hours weekly"
}

# Used when a preparation has no stated pace, which is every row created before
# migration 007. Matches the middle option rather than the most demanding one.
DEFAULT_PACE: PreparationPace = "BALANCED"

# The scheduler's existing ceiling. Pace may lower the daily budget but never
# raise it past what the codebase already considered sustainable.
MAX_SUSTAINABLE_DAILY_MINUTES = 120


def sessions_per_week(pace: str | None) -> int:
    """How many study sessions a week the chosen pace implies."""
    return _PACE_SESSIONS_PER_WEEK.get(
        (pace or DEFAULT_PACE).upper(), _PACE_SESSIONS_PER_WEEK[DEFAULT_PACE]
    )


def weekly_minutes(pace: str | None) -> int:
    """Total weekly minutes the chosen pace implies."""
    return _PACE_WEEKLY_MINUTES.get(
        (pace or DEFAULT_PACE).upper(), _PACE_WEEKLY_MINUTES[DEFAULT_PACE]
    )


def daily_minute_budget(pace: str | None, *, behaviour_minutes: float | None = None) -> float:
    """The daily minute budget for scheduling, given a pace.

    `behaviour_minutes` is the learner's observed average session length. Where
    both exist the **smaller** budget wins: a learner who asks for an intensive
    pace but has never sustained more than 20 minutes should not be handed a plan
    built for someone who has. Stated intent can pull the budget down but not past
    what their behaviour supports, which is the difference between an ambitious
    plan and an abandoned one.

    With no pace and no behaviour data this returns the same value the scheduler
    used before pace existed, so nothing changes for preparations that have neither.
    """
    from_pace = weekly_minutes(pace) / 7

    if behaviour_minutes:
        # The pre-existing rule: a sustainable day is about 1.5 sessions.
        from_behaviour = min(behaviour_minutes * 1.5, MAX_SUSTAINABLE_DAILY_MINUTES)
        return min(from_pace, from_behaviour)

    return min(from_pace, MAX_SUSTAINABLE_DAILY_MINUTES)
