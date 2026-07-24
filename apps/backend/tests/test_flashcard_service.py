"""Unit tests for flashcard SM-2 algorithm pure computation (no DB required)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from dataclasses import dataclass

import pytest


# ---------------------------------------------------------------------------
# Helper: minimal card dataclass that mimics Flashcard SM-2 attributes
# ---------------------------------------------------------------------------


@dataclass
class FakeCard:
    interval_days: int = 1
    repetition_count: int = 0
    ease_factor: float = 2.5
    lapse_count: int = 0
    last_quality: int = -1


# ---------------------------------------------------------------------------
# Pure SM-2 computation extracted from flashcard_service.review_flashcard
# ---------------------------------------------------------------------------


def compute_sm2(card: FakeCard, quality: int) -> dict:
    """
    Pure SM-2 computation — mirrors the logic in flashcard_service.review_flashcard.

    Returns dict with: interval, repetition, ease_factor, lapse_count
    """
    new_ease = card.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ease = max(new_ease, 1.3)

    if quality < 3:
        new_interval = 1
        new_repetition = 0
        new_lapse = card.lapse_count + 1
    else:
        new_lapse = card.lapse_count
        if card.repetition_count == 0:
            new_interval = 1
        elif card.repetition_count == 1:
            new_interval = 6
        else:
            new_interval = round(card.interval_days * new_ease)
        new_repetition = card.repetition_count + 1

    return {
        "interval": new_interval,
        "repetition": new_repetition,
        "ease_factor": round(new_ease, 4),
        "lapse_count": new_lapse,
    }


# ---------------------------------------------------------------------------
# TestSM2Computation
# ---------------------------------------------------------------------------


class TestSM2Computation:
    """Tests for the core SM-2 review algorithm."""

    def test_quality_0_resets_interval(self):
        card = FakeCard(interval_days=10, repetition_count=5, ease_factor=2.5, lapse_count=0)
        result = compute_sm2(card, quality=0)
        assert result["interval"] == 1
        assert result["lapse_count"] == 1

    def test_quality_1_resets_interval(self):
        card = FakeCard(interval_days=15, repetition_count=4, ease_factor=2.2, lapse_count=1)
        result = compute_sm2(card, quality=1)
        assert result["interval"] == 1
        assert result["lapse_count"] == 2

    def test_quality_2_resets_interval(self):
        card = FakeCard(interval_days=20, repetition_count=6, ease_factor=2.8, lapse_count=0)
        result = compute_sm2(card, quality=2)
        assert result["interval"] == 1
        assert result["lapse_count"] == 1

    def test_quality_3_first_review(self):
        card = FakeCard(interval_days=1, repetition_count=0, ease_factor=2.5)
        result = compute_sm2(card, quality=3)
        assert result["interval"] == 1
        assert result["repetition"] == 1

    def test_quality_3_second_review(self):
        card = FakeCard(interval_days=1, repetition_count=1, ease_factor=2.5)
        result = compute_sm2(card, quality=3)
        assert result["interval"] == 6
        assert result["repetition"] == 2

    def test_quality_3_subsequent_review(self):
        card = FakeCard(interval_days=6, repetition_count=2, ease_factor=2.5)
        result = compute_sm2(card, quality=3)
        # new_ease = 2.5 - 0.14 = 2.36, interval = round(6 * 2.36) = round(14.16) = 14
        assert result["interval"] == 14
        assert result["repetition"] == 3

    def test_quality_4_increases_ease(self):
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=4)
        # EF' = 2.5 + (0.1 - (5-4)*(0.08 + (5-4)*0.02)) = 2.5 + 0.0 = 2.5
        assert result["ease_factor"] == 2.5

    def test_quality_5_increases_ease_most(self):
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=5)
        # EF' = 2.5 + (0.1 - 0*(0.08 + 0*0.02)) = 2.5 + 0.1 = 2.6
        assert result["ease_factor"] == 2.6

    def test_ease_never_below_1_3(self):
        # Start with low ease and repeatedly fail
        card = FakeCard(ease_factor=1.3, lapse_count=5)
        result = compute_sm2(card, quality=0)
        assert result["ease_factor"] >= 1.3

        # Even with very low ease, it should clamp
        card2 = FakeCard(ease_factor=1.3)
        result2 = compute_sm2(card2, quality=1)
        assert result2["ease_factor"] >= 1.3

    def test_lapse_increments_on_failure(self):
        card = FakeCard(lapse_count=3)
        result = compute_sm2(card, quality=2)
        assert result["lapse_count"] == 4

    def test_lapse_stays_on_success(self):
        card = FakeCard(lapse_count=3)
        result = compute_sm2(card, quality=3)
        assert result["lapse_count"] == 3

    def test_repetition_resets_on_failure(self):
        card = FakeCard(repetition_count=5)
        result = compute_sm2(card, quality=1)
        assert result["repetition"] == 0

    def test_repetition_increments_on_success(self):
        card = FakeCard(repetition_count=5, interval_days=10, ease_factor=2.5)
        result = compute_sm2(card, quality=4)
        assert result["repetition"] == 6

    def test_interval_monotonically_increases(self):
        """Consecutive quality=4 reviews should produce growing intervals."""
        card = FakeCard(interval_days=1, repetition_count=0, ease_factor=2.5)
        intervals = []

        for _ in range(6):
            result = compute_sm2(card, quality=4)
            intervals.append(result["interval"])
            card = FakeCard(
                interval_days=result["interval"],
                repetition_count=result["repetition"],
                ease_factor=result["ease_factor"],
                lapse_count=result["lapse_count"],
            )

        # After the initial 1 and 6, intervals should keep growing
        # intervals[0] = 1 (first review), intervals[1] = 6 (second), then growing
        assert intervals[0] == 1
        assert intervals[1] == 6
        for i in range(2, len(intervals) - 1):
            assert intervals[i + 1] >= intervals[i]


# ---------------------------------------------------------------------------
# TestFlashcardInitialization
# ---------------------------------------------------------------------------


class TestFlashcardInitialization:
    """Tests for SM-2 default values on new flashcards."""

    def test_new_card_has_correct_sm2_defaults(self):
        card = FakeCard()
        assert card.interval_days == 1
        assert card.repetition_count == 0
        assert card.ease_factor == 2.5
        assert card.lapse_count == 0
        assert card.last_quality == -1


# ---------------------------------------------------------------------------
# TestEaseFactorFormula
# ---------------------------------------------------------------------------


class TestEaseFactorFormula:
    """Tests for the exact ease factor adjustment formula.

    Formula: EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    """

    def test_ease_with_quality_5(self):
        """EF + 0.1 (maximum increase)."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=5)
        # delta = 0.1 - 0*(0.08 + 0*0.02) = 0.1
        assert result["ease_factor"] == pytest.approx(2.6, abs=1e-4)

    def test_ease_with_quality_4(self):
        """EF + 0.0 (no change)."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=4)
        # delta = 0.1 - 1*(0.08 + 1*0.02) = 0.1 - 0.10 = 0.0
        assert result["ease_factor"] == pytest.approx(2.5, abs=1e-4)

    def test_ease_with_quality_3(self):
        """EF - 0.14."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=3)
        # delta = 0.1 - 2*(0.08 + 2*0.02) = 0.1 - 2*0.12 = 0.1 - 0.24 = -0.14
        assert result["ease_factor"] == pytest.approx(2.36, abs=1e-4)

    def test_ease_with_quality_2(self):
        """EF - 0.32."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=2)
        # delta = 0.1 - 3*(0.08 + 3*0.02) = 0.1 - 3*0.14 = 0.1 - 0.42 = -0.32
        assert result["ease_factor"] == pytest.approx(2.18, abs=1e-4)

    def test_ease_with_quality_1(self):
        """EF - 0.54."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=1)
        # delta = 0.1 - 4*(0.08 + 4*0.02) = 0.1 - 4*0.16 = 0.1 - 0.64 = -0.54
        assert result["ease_factor"] == pytest.approx(1.96, abs=1e-4)

    def test_ease_with_quality_0(self):
        """EF - 0.80."""
        card = FakeCard(ease_factor=2.5)
        result = compute_sm2(card, quality=0)
        # delta = 0.1 - 5*(0.08 + 5*0.02) = 0.1 - 5*0.18 = 0.1 - 0.90 = -0.80
        assert result["ease_factor"] == pytest.approx(1.7, abs=1e-4)
