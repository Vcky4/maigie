"""Unit tests for the shared preparation readiness logic (no DB required).

This module is the single definition of the mastery ladder and of every number
the Prepare surface and the Learn dashboard report about a preparation, so a
regression here makes two surfaces disagree at once.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import date

import pytest

from src.domains.personal_learning.services.prep_readiness import (
    MASTERY_FOCUS_THRESHOLD,
    MASTERY_STRONG_THRESHOLD,
    PrepProgress,
    mastery_band,
    practice_streak,
)


def _progress(**overrides) -> PrepProgress:
    defaults = {
        "topics_total": 0,
        "topics_strong": 0,
        "topics_focus": 0,
        "topics_assessed": 0,
        "questions_answered": 0,
        "questions_correct": 0,
        "quizzes_taken": 0,
        "practice_seconds": 0,
        "mastery_sum": 0.0,
    }
    return PrepProgress(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# TestMasteryBand
# ---------------------------------------------------------------------------


class TestMasteryBand:
    """Three bands at two pre-existing boundaries: 70 (WEAK_AREAS) and 80 (MASTERED)."""

    def test_below_focus_threshold_is_focus(self):
        assert mastery_band(0.0) == "focus"
        assert mastery_band(69.9) == "focus"

    def test_focus_threshold_is_inclusive_of_review(self):
        assert mastery_band(MASTERY_FOCUS_THRESHOLD) == "review"

    def test_between_thresholds_is_review(self):
        assert mastery_band(75.0) == "review"
        assert mastery_band(79.9) == "review"

    def test_strong_threshold_is_inclusive(self):
        assert mastery_band(MASTERY_STRONG_THRESHOLD) == "strong"

    def test_above_strong_threshold_is_strong(self):
        assert mastery_band(100.0) == "strong"

    def test_none_is_treated_as_unpractised(self):
        """A topic with no mastery recorded needs work, not credit."""
        assert mastery_band(None) == "focus"

    def test_thresholds_match_the_values_the_codebase_already_used(self):
        assert MASTERY_FOCUS_THRESHOLD == 70.0
        assert MASTERY_STRONG_THRESHOLD == 80.0


# ---------------------------------------------------------------------------
# TestProgressPercent
# ---------------------------------------------------------------------------


class TestProgressPercent:
    """The headline number, shared with the Learn dashboard."""

    def test_no_topics_is_zero(self):
        assert _progress().progress_percent == 0.0

    def test_all_strong_is_one_hundred(self):
        assert _progress(topics_total=5, topics_strong=5).progress_percent == 100.0

    def test_half_strong(self):
        assert _progress(topics_total=4, topics_strong=2).progress_percent == 50.0

    @pytest.mark.parametrize("total", range(1, 13))
    def test_matches_the_learn_card_unit_counts(self, total):
        """The invariant that forces this formula.

        A Learn path card renders the percent directly above "x / y complete", so
        the percent has to be exactly ``strong / total``. Any other ratio makes
        the card contradict itself.
        """
        for strong in range(total + 1):
            progress = _progress(topics_total=total, topics_strong=strong)
            assert progress.progress_percent == round((strong / total) * 100, 1)
            assert progress.topics_strong == strong
            assert progress.topics_total == total


# ---------------------------------------------------------------------------
# TestAverageMastery
# ---------------------------------------------------------------------------


class TestAverageMastery:
    def test_none_when_there_are_no_topics(self):
        """Not measured, rather than measured as zero."""
        assert _progress().average_mastery_percent is None

    def test_mean_over_all_topics(self):
        assert _progress(topics_total=4, mastery_sum=200.0).average_mastery_percent == 50.0

    def test_includes_unpractised_topics(self):
        """Averaging only practised topics would overstate exam readiness."""
        # Four topics, two at 100 and two never practised.
        assert _progress(topics_total=4, mastery_sum=200.0).average_mastery_percent == 50.0

    def test_clamped_to_one_hundred(self):
        assert _progress(topics_total=1, mastery_sum=150.0).average_mastery_percent == 100.0

    def test_differs_from_progress_percent_and_that_is_intended(self):
        """The documented example: both are correct answers to different questions."""
        progress = _progress(topics_total=12, topics_strong=6, mastery_sum=6 * 100 + 6 * 79)
        assert progress.progress_percent == 50.0
        assert progress.average_mastery_percent == 89.5


# ---------------------------------------------------------------------------
# TestAccuracyAndReadiness
# ---------------------------------------------------------------------------


class TestAccuracyAndReadiness:
    def test_accuracy_is_none_before_any_answer(self):
        assert _progress().accuracy_percent is None

    def test_accuracy_zero_is_distinct_from_unmeasured(self):
        progress = _progress(questions_answered=5, questions_correct=0)
        assert progress.accuracy_percent == 0.0

    def test_accuracy_ratio(self):
        assert _progress(questions_answered=4, questions_correct=3).accuracy_percent == 75.0

    def test_practice_not_ready_without_topics(self):
        """Quiz generation needs topics, so the UI must route to extraction first."""
        assert _progress().practice_ready is False

    def test_practice_ready_with_topics(self):
        assert _progress(topics_total=1).practice_ready is True


# ---------------------------------------------------------------------------
# TestPracticeStreak
# ---------------------------------------------------------------------------


TODAY = date(2026, 8, 7)


class TestPracticeStreak:
    """Decision I: consecutive days with a *completed* quiz session."""

    def test_never_practised_is_none(self):
        """Distinct from a lapsed streak."""
        assert practice_streak([], today=TODAY) is None

    def test_practised_today_is_one(self):
        assert practice_streak([TODAY], today=TODAY) == 1

    def test_practised_yesterday_still_counts(self):
        """Not yet practised today is not a broken streak; treating it as zero
        would manufacture urgency, which Decision I rules out."""
        assert practice_streak([date(2026, 8, 6)], today=TODAY) == 1

    def test_consecutive_days_accumulate(self):
        days = [date(2026, 8, 7), date(2026, 8, 6), date(2026, 8, 5)]
        assert practice_streak(days, today=TODAY) == 3

    def test_streak_running_from_yesterday_backwards(self):
        days = [date(2026, 8, 6), date(2026, 8, 5), date(2026, 8, 4)]
        assert practice_streak(days, today=TODAY) == 3

    def test_gap_ends_the_streak(self):
        days = [date(2026, 8, 7), date(2026, 8, 6), date(2026, 8, 3), date(2026, 8, 2)]
        assert practice_streak(days, today=TODAY) == 2

    def test_lapsed_streak_is_zero(self):
        """Two or more days since the last session."""
        assert practice_streak([date(2026, 8, 4)], today=TODAY) == 0

    def test_multiple_sessions_on_one_day_count_once(self):
        days = [TODAY, TODAY, TODAY]
        assert practice_streak(days, today=TODAY) == 1

    def test_unordered_input_is_handled(self):
        days = [date(2026, 8, 5), date(2026, 8, 7), date(2026, 8, 6)]
        assert practice_streak(days, today=TODAY) == 3

    def test_long_streak_across_a_month_boundary(self):
        days = [date(2026, 8, 2), date(2026, 8, 1), date(2026, 7, 31), date(2026, 7, 30)]
        assert practice_streak(days, today=date(2026, 8, 2)) == 4

    def test_future_dated_session_does_not_break_the_calculation(self):
        """Clock skew should not produce a negative or crashing result."""
        result = practice_streak([date(2026, 8, 8), TODAY], today=TODAY)
        assert result is not None
        assert result >= 1
