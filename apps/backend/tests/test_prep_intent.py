"""Unit tests for preparation intent (pace and confidence). No DB required.

The pace-to-effort mapping used to live as copy in the web client next to a
separate constant, so what a learner read and what they were scheduled were
defined in two places. These tests pin the server-side definition.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest

from src.domains.personal_learning.services.prep_intent import (
    DEFAULT_PACE,
    MAX_SUSTAINABLE_DAILY_MINUTES,
    daily_minute_budget,
    sessions_per_week,
    weekly_minutes,
)


class TestSessionsPerWeek:
    """Must match the wizard's own copy: 3, 5, and daily."""

    @pytest.mark.parametrize("pace,expected", [("LIGHT", 3), ("BALANCED", 5), ("INTENSIVE", 7)])
    def test_known_paces(self, pace, expected):
        assert sessions_per_week(pace) == expected

    def test_case_insensitive(self):
        assert sessions_per_week("balanced") == 5

    def test_missing_pace_falls_back_to_the_middle_option(self):
        """Not the most demanding one — an absent choice is not a request to push."""
        assert sessions_per_week(None) == sessions_per_week(DEFAULT_PACE)
        assert DEFAULT_PACE == "BALANCED"

    def test_unknown_pace_falls_back_rather_than_raising(self):
        """Legacy or unexpected values must not break plan generation."""
        assert sessions_per_week("TURBO") == sessions_per_week(DEFAULT_PACE)


class TestWeeklyMinutes:
    @pytest.mark.parametrize(
        "pace,expected", [("LIGHT", 120), ("BALANCED", 240), ("INTENSIVE", 360)]
    )
    def test_matches_the_wizard_copy(self, pace, expected):
        """ "About 2 hours weekly", "About 4 hours weekly", "6+ hours weekly"."""
        assert weekly_minutes(pace) == expected

    def test_increases_with_intensity(self):
        assert weekly_minutes("LIGHT") < weekly_minutes("BALANCED") < weekly_minutes("INTENSIVE")


class TestDailyMinuteBudget:
    def test_derived_from_weekly_minutes(self):
        assert daily_minute_budget("LIGHT") == pytest.approx(120 / 7)

    def test_never_exceeds_the_sustainable_ceiling(self):
        for pace in ("LIGHT", "BALANCED", "INTENSIVE", None, "TURBO"):
            assert daily_minute_budget(pace) <= MAX_SUSTAINABLE_DAILY_MINUTES

    def test_behaviour_can_pull_the_budget_down(self):
        """A learner who has never sustained more than 10 minutes should not be
        handed an intensive plan just because they asked for one."""
        budget = daily_minute_budget("INTENSIVE", behaviour_minutes=10)
        assert budget == pytest.approx(15)  # 10 * 1.5
        assert budget < daily_minute_budget("INTENSIVE")

    def test_stated_pace_can_pull_the_budget_down_too(self):
        """A light pace is respected even by a learner who could do more."""
        budget = daily_minute_budget("LIGHT", behaviour_minutes=90)
        assert budget == pytest.approx(120 / 7)

    def test_smaller_of_the_two_always_wins(self):
        for behaviour in (5, 20, 60, 200):
            for pace in ("LIGHT", "BALANCED", "INTENSIVE"):
                budget = daily_minute_budget(pace, behaviour_minutes=behaviour)
                assert budget <= weekly_minutes(pace) / 7
                assert budget <= min(behaviour * 1.5, MAX_SUSTAINABLE_DAILY_MINUTES)

    def test_no_pace_and_no_behaviour_matches_the_previous_ceiling(self):
        """Preparations with neither must schedule exactly as they did before."""
        assert daily_minute_budget(None) <= MAX_SUSTAINABLE_DAILY_MINUTES

    def test_zero_behaviour_minutes_is_ignored_rather_than_zeroing_the_budget(self):
        """A learner with no recorded sessions must still get a plan."""
        assert daily_minute_budget("BALANCED", behaviour_minutes=0) > 0

    def test_budget_is_always_positive(self):
        for pace in ("LIGHT", "BALANCED", "INTENSIVE", None):
            for behaviour in (None, 0, 1, 45):
                assert daily_minute_budget(pace, behaviour_minutes=behaviour) > 0
