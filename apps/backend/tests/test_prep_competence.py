"""Unit tests for the competence model (no DB required).

This replaced a lifetime average that the book rules out twice — it never forgot a
bad week, and it let one mistake stand as a whole assessment. These tests pin the
properties that make the replacement different, not just its arithmetic.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services.prep_competence import (
    MIN_OBSERVATIONS,
    RECENCY_HALF_LIFE_DAYS,
    estimate,
    response_baseline,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _obs(
    *,
    correct: bool = True,
    days_ago: float = 0,
    hints: int = 0,
    difficulty: str | None = "MEDIUM",
    response_ms: int | None = None,
):
    return SimpleNamespace(
        prep_topic_id="topic-1",
        is_correct=correct,
        observed_at=NOW - timedelta(days=days_ago),
        hint_count=hints,
        difficulty=difficulty,
        response_ms=response_ms,
    )


def _many(count: int, **kwargs):
    return [_obs(**kwargs) for _ in range(count)]


# ---------------------------------------------------------------------------
# TestEvidenceThreshold
# ---------------------------------------------------------------------------


class TestEvidenceThreshold:
    """A single mistake is not a weakness, so one answer is not an assessment."""

    def test_no_observations_is_not_measurable(self):
        result = estimate([], topic_id="topic-1", now=NOW)
        assert result.is_measurable is False
        assert result.retention is None
        assert result.observations == 0

    def test_one_observation_is_not_measurable(self):
        result = estimate([_obs(correct=False)], now=NOW)
        assert result.is_measurable is False
        # The crucial part: it does NOT report 0%, which would tell a learner they
        # know nothing on the strength of one wrong answer.
        assert result.retention is None

    def test_below_the_threshold_stays_unmeasurable(self):
        result = estimate(_many(MIN_OBSERVATIONS - 1), now=NOW)
        assert result.is_measurable is False

    def test_at_the_threshold_becomes_measurable(self):
        result = estimate(_many(MIN_OBSERVATIONS), now=NOW)
        assert result.is_measurable is True
        assert result.retention is not None

    def test_enough_but_stale_observations_are_not_measurable(self):
        """Three answers from two months ago are not a current assessment."""
        result = estimate(_many(MIN_OBSERVATIONS, days_ago=90), now=NOW)
        assert result.is_measurable is False

    def test_counts_are_reported_even_when_unmeasurable(self):
        """So a surface can say "based on 2 questions" rather than nothing at all."""
        result = estimate(_many(2), now=NOW)
        assert result.observations == 2
        assert result.effective_weight > 0

    def test_an_unmeasured_topic_needs_attention(self):
        """Not knowing is a reason to look, not a reason to skip."""
        assert estimate([], now=NOW).needs_attention is True

    def test_band_is_none_when_unmeasurable(self):
        assert estimate(_many(1), now=NOW).band is None


# ---------------------------------------------------------------------------
# TestRecency
# ---------------------------------------------------------------------------


class TestRecency:
    """Never trap someone in their past — decay is what makes that true."""

    def test_all_correct_today_is_full_retention(self):
        assert estimate(_many(4, correct=True), now=NOW).retention == 100.0

    def test_all_wrong_today_is_zero_retention(self):
        assert estimate(_many(4, correct=False), now=NOW).retention == 0.0

    def test_recent_success_outweighs_old_failure(self):
        """The exact case the lifetime average got wrong: ten wrong last month, ten
        right today used to read 50%, indistinguishable from guessing."""
        observations = _many(10, correct=False, days_ago=30) + _many(10, correct=True, days_ago=0)
        result = estimate(observations, now=NOW)
        assert result.retention is not None
        assert result.retention > 75.0

    def test_old_success_does_not_mask_recent_failure(self):
        """Decay has to cut both ways or it is just optimism."""
        observations = _many(10, correct=True, days_ago=30) + _many(10, correct=False, days_ago=0)
        result = estimate(observations, now=NOW)
        assert result.retention is not None
        assert result.retention < 25.0

    def test_a_half_life_old_observation_counts_about_half(self):
        fresh = estimate(_many(6, correct=True), now=NOW).effective_weight
        aged = estimate(
            _many(6, correct=True, days_ago=RECENCY_HALF_LIFE_DAYS), now=NOW
        ).effective_weight
        assert aged == pytest.approx(fresh / 2, rel=0.02)

    def test_evidence_a_few_half_lives_old_still_counts(self):
        """Fading, not vanishing — two half-lives should still carry real weight."""
        result = estimate(_many(5, days_ago=RECENCY_HALF_LIFE_DAYS * 2), now=NOW)
        assert result.effective_weight == pytest.approx(5 * 0.25, rel=0.02)

    def test_year_old_evidence_cannot_support_a_conclusion(self):
        """Its weight rounds to nothing, and it must not divide by zero on the way."""
        result = estimate(_many(5, days_ago=365), now=NOW)
        assert result.is_measurable is False
        assert result.retention is None
        assert result.observations == 5

    def test_future_dated_observations_do_not_break_weighting(self):
        """Clock skew should not produce a negative age or a weight above one."""
        result = estimate(_many(4, correct=True, days_ago=-3), now=NOW)
        assert result.retention == 100.0


# ---------------------------------------------------------------------------
# TestDifficultyWeighting
# ---------------------------------------------------------------------------


class TestDifficultyWeighting:
    def test_hard_correct_beats_easy_correct(self):
        hard = estimate(
            _many(3, correct=True, difficulty="HARD") + _many(3, correct=False, difficulty="EASY"),
            now=NOW,
        )
        easy = estimate(
            _many(3, correct=True, difficulty="EASY") + _many(3, correct=False, difficulty="HARD"),
            now=NOW,
        )
        assert hard.retention > easy.retention

    def test_unknown_difficulty_is_treated_as_medium(self):
        unknown = estimate(_many(4, correct=True, difficulty=None), now=NOW)
        medium = estimate(_many(4, correct=True, difficulty="MEDIUM"), now=NOW)
        assert unknown.retention == medium.retention

    def test_difficulty_is_case_insensitive(self):
        lower = estimate(_many(4, correct=True, difficulty="hard"), now=NOW)
        upper = estimate(_many(4, correct=True, difficulty="HARD"), now=NOW)
        assert lower.retention == upper.retention


# ---------------------------------------------------------------------------
# TestHintDiscount
# ---------------------------------------------------------------------------


class TestHintDiscount:
    """A hinted-correct answer is evidence of *assisted* competence."""

    def test_hinted_correct_is_worth_less_than_unaided(self):
        unaided = estimate(_many(4, correct=True, hints=0), now=NOW)
        hinted = estimate(_many(4, correct=True, hints=1), now=NOW)
        assert hinted.retention < unaided.retention

    def test_hinted_correct_is_worth_more_than_wrong(self):
        """It is not a wrong answer and must never be scored as one."""
        hinted = estimate(_many(4, correct=True, hints=2), now=NOW)
        wrong = estimate(_many(4, correct=False), now=NOW)
        assert hinted.retention > wrong.retention

    def test_more_hints_means_less_credit(self):
        one = estimate(_many(4, correct=True, hints=1), now=NOW).retention
        two = estimate(_many(4, correct=True, hints=2), now=NOW).retention
        assert two < one

    def test_hints_on_a_wrong_answer_change_nothing(self):
        """Wrong is wrong; the hint does not make it worse."""
        plain = estimate(_many(4, correct=False, hints=0), now=NOW).retention
        hinted = estimate(_many(4, correct=False, hints=3), now=NOW).retention
        assert plain == hinted == 0.0

    def test_independence_reflects_unaided_answers(self):
        result = estimate(
            _many(2, correct=True, hints=0) + _many(2, correct=True, hints=1), now=NOW
        )
        assert result.independence == pytest.approx(50.0, abs=1.0)

    def test_full_independence_with_no_hints(self):
        assert estimate(_many(4, correct=True, hints=0), now=NOW).independence == 100.0

    def test_no_independence_when_every_answer_was_hinted(self):
        assert estimate(_many(4, correct=True, hints=1), now=NOW).independence == 0.0


# ---------------------------------------------------------------------------
# TestReliability
# ---------------------------------------------------------------------------


class TestReliability:
    """Consistency, not skill. A scattered learner's headline number hides that."""

    def test_consistent_answers_are_reliable(self):
        assert estimate(_many(6, correct=True), now=NOW).reliability == 100.0

    def test_consistently_wrong_is_also_reliable(self):
        """We are confident about it — the estimate is trustworthy, not the learner."""
        assert estimate(_many(6, correct=False), now=NOW).reliability == 100.0

    def test_mixed_answers_are_less_reliable(self):
        mixed = estimate(_many(3, correct=True) + _many(3, correct=False), now=NOW)
        assert mixed.reliability < 100.0


# ---------------------------------------------------------------------------
# TestFluency
# ---------------------------------------------------------------------------


class TestFluency:
    """Relative to the learner's own baseline — a slow reader is not a weak learner."""

    def test_none_without_timing_data(self):
        assert estimate(_many(4), now=NOW, baseline_ms=5000).fluency is None

    def test_none_without_a_baseline(self):
        """Not zero, which would read as maximally laboured."""
        result = estimate(_many(4, response_ms=5000), now=NOW, baseline_ms=None)
        assert result.fluency is None

    def test_at_baseline_is_fluent(self):
        result = estimate(_many(4, response_ms=5000), now=NOW, baseline_ms=5000)
        assert result.fluency == 100.0

    def test_faster_than_baseline_is_fluent(self):
        result = estimate(_many(4, response_ms=2000), now=NOW, baseline_ms=5000)
        assert result.fluency == 100.0

    def test_much_slower_than_baseline_is_laboured(self):
        result = estimate(_many(4, response_ms=20000), now=NOW, baseline_ms=5000)
        assert result.fluency == 0.0

    def test_moderately_slower_is_in_between(self):
        result = estimate(_many(4, response_ms=10000), now=NOW, baseline_ms=5000)
        assert 0.0 < result.fluency < 100.0

    def test_fluency_is_independent_of_correctness(self):
        """Answering wrong quickly and wrong slowly are different states."""
        fast = estimate(_many(4, correct=False, response_ms=2000), now=NOW, baseline_ms=5000)
        slow = estimate(_many(4, correct=False, response_ms=20000), now=NOW, baseline_ms=5000)
        assert fast.fluency > slow.fluency
        assert fast.retention == slow.retention == 0.0


# ---------------------------------------------------------------------------
# TestResponseBaseline
# ---------------------------------------------------------------------------


class TestResponseBaseline:
    def test_none_without_enough_timings(self):
        assert response_baseline(_many(2, response_ms=5000)) is None

    def test_median_of_timings(self):
        observations = [
            _obs(response_ms=1000),
            _obs(response_ms=5000),
            _obs(response_ms=9000),
        ]
        assert response_baseline(observations) == 5000.0

    def test_the_median_resists_one_abandoned_question(self):
        """A learner who walked away mid-question must not redefine their normal."""
        observations = [
            _obs(response_ms=4000),
            _obs(response_ms=5000),
            _obs(response_ms=6000),
            _obs(response_ms=3_600_000),
        ]
        assert response_baseline(observations) < 10_000

    def test_untimed_observations_are_ignored(self):
        observations = _many(3, response_ms=None) + _many(3, response_ms=5000)
        assert response_baseline(observations) == 5000.0


# ---------------------------------------------------------------------------
# TestBands
# ---------------------------------------------------------------------------


class TestBands:
    """The ladder is unchanged; only the number feeding it improved."""

    def test_strong_when_retention_is_high(self):
        assert estimate(_many(6, correct=True), now=NOW).band == "strong"

    def test_focus_when_retention_is_low(self):
        assert estimate(_many(6, correct=False), now=NOW).band == "focus"

    def test_focus_needs_attention(self):
        assert estimate(_many(6, correct=False), now=NOW).needs_attention is True

    def test_strong_does_not_need_attention(self):
        assert estimate(_many(6, correct=True), now=NOW).needs_attention is False
