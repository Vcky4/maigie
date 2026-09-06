"""Tests for the persisted quiz generation duration and the percentile it feeds.

Decision H's trigger — p95 start latency above 10s — was cited three times in the
Prepare plan and never read, because `generation_ms` was emitted only as a log
field. Migration `018` persists it. Two things are worth guarding:

- **The repository accepts the key.** `_map_quiz_session` silently drops anything
  not in its field map, which is the same failure that made `ExamPrep.type`
  accepted-and-discarded through several phases. A dropped key here means the
  column stays `NULL` forever and the reading is never taken, with nothing failing.
- **The percentile is nearest-rank.** An interpolating percentile reports a
  duration nobody measured, and the number decides whether an architecture changes.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import importlib.util
from pathlib import Path

import pytest

from src.domains.personal_learning.repository import PersonalLearningRepository
from src.shared.field_mapping import UnmappedFieldError, map_fields

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_generation_latency.py"
_spec = importlib.util.spec_from_file_location("check_generation_latency", _SCRIPT)
assert _spec and _spec.loader
check_generation_latency = importlib.util.module_from_spec(_spec)

# The script calls `load_dotenv()` at import, which is right for a script and wrong
# here: executing it would copy `.env` — including `DATABASE_URL` — into `os.environ`
# for the whole pytest process. Because pytest imports every module during collection,
# that quietly pointed the entire suite at the real database, and any test that decides
# what to do based on `DATABASE_URL` would then decide differently depending on whether
# this file happened to be collected. Neutralised for the duration of the import; the
# functions under test take their inputs as arguments and need no environment.
_dotenv = importlib.import_module("dotenv")
_real_load_dotenv = _dotenv.load_dotenv
_dotenv.load_dotenv = lambda *args, **kwargs: False
try:
    _spec.loader.exec_module(check_generation_latency)
finally:
    _dotenv.load_dotenv = _real_load_dotenv

percentile = check_generation_latency.percentile


class TestSessionFieldMapping:
    """The column is only useful if writes reach it."""

    def test_generation_ms_reaches_the_column(self):
        mapped = PersonalLearningRepository._map_quiz_session({"generationMs": 4321})
        assert mapped == {"generation_ms": 4321}

    def test_it_travels_with_the_status_update_that_carries_it(self):
        # Both call sites write it alongside the status transition, so the whole
        # payload has to survive the mapping together.
        mapped = PersonalLearningRepository._map_quiz_session(
            {"status": "IN_PROGRESS", "totalQuestions": 10, "generationMs": 8123}
        )
        assert mapped == {
            "status": "IN_PROGRESS",
            "total_questions": 10,
            "generation_ms": 8123,
        }

    def test_the_failure_path_payload_also_survives(self):
        """A start that spent 40s and produced nothing is the most important
        reading there is; recording it only on success would bias the percentile
        towards the fast attempts."""
        mapped = PersonalLearningRepository._map_quiz_session(
            {"status": "FAILED", "totalQuestions": 0, "generationMs": 40_000}
        )
        assert mapped["status"] == "FAILED"
        assert mapped["generation_ms"] == 40_000

    def test_an_unknown_key_is_refused_rather_than_dropped(self):
        """The permissiveness that hid the `type` defect is now an error.

        This test previously asserted the opposite: that an unmapped key was silently discarded, with
        a docstring noting that the same permissiveness had already hidden a defect. Pinning that
        behaviour in place made the hazard permanent — a field added to a request model and not to the
        mapper was accepted, dropped, and reported as success.

        Three more instances were found later, each costing real data: `search` on the course list,
        five fields on the course create form, and the three flashcard review aids. So the mapper now
        refuses, and this test guards the refusal.
        """
        with pytest.raises(UnmappedFieldError, match="notAColumn"):
            PersonalLearningRepository._map_quiz_session({"notAColumn": 1})

    def test_a_field_can_be_ignored_deliberately(self):
        """The escape hatch, so strictness does not force a fake column.

        A field handled somewhere other than this mapper is passed in `ignore` at the call site, which
        keeps the decision visible. Silence and an explicit exemption look different in a diff.
        """
        assert (
            map_fields({"notAColumn": 1}, {"status": "status"}, entity="t", ignore={"notAColumn"})
            == {}
        )

    def test_the_column_is_nullable_with_no_default(self):
        """Sessions predating `018` must read as unknown, not as instantaneous.

        `0` would be a duration nobody observed, dragging every percentile down —
        the exact distortion that would keep Decision H's trigger from firing.
        """
        from src.domains.personal_learning.db_models import QuizSession

        column = QuizSession.__table__.c["generationMs"]
        assert column.nullable is True
        assert column.default is None
        assert column.server_default is None


class TestPercentile:
    def test_nearest_rank_returns_an_observed_value(self):
        values = [100, 200, 300, 400]
        # Every result is a number that was actually measured.
        assert percentile(values, 0.50) in values
        assert percentile(values, 0.95) in values

    def test_p95_of_twenty_picks_the_nineteenth(self):
        values = list(range(1, 21))
        assert percentile(values, 0.95) == 19

    def test_p50_of_an_even_count_does_not_average(self):
        # An interpolating percentile would answer 250.
        assert percentile([100, 200, 300, 400], 0.50) == 200

    def test_order_does_not_matter(self):
        assert percentile([400, 100, 300, 200], 0.75) == percentile([100, 200, 300, 400], 0.75)

    def test_a_single_sample_is_itself(self):
        assert percentile([7], 0.95) == 7

    def test_empty_is_zero_rather_than_an_error(self):
        # The script prints coverage before it prints percentiles, so an empty set
        # is reported as "no timings recorded" rather than reaching this.
        assert percentile([], 0.95) == 0

    def test_the_top_percentile_never_runs_off_the_end(self):
        for size in range(1, 40):
            values = list(range(size))
            assert percentile(values, 1.0) == values[-1]

    def test_p95_never_reports_the_maximum_once_there_are_enough_samples(self):
        """The bug this caught: `round(p * N + 0.5)` overshot on an exact rank, so
        p95 over 20 samples returned the slowest start on record. One 40s outlier
        would then have read as the 95th percentile and tripped Decision H.
        """
        values = [10] * 19 + [40_000]
        assert percentile(values, 0.95) == 10
        assert max(values) == 40_000

    @pytest.mark.parametrize("fraction", [0.5, 0.75, 0.9, 0.95])
    def test_percentiles_are_monotonic_in_the_data(self, fraction):
        low = [10] * 30
        high = [10] * 29 + [50_000]
        assert percentile(high, fraction) >= percentile(low, fraction)


class TestVerdictThresholds:
    def test_the_trigger_matches_the_decision(self):
        assert check_generation_latency.P95_TRIGGER_MS == 10_000

    def test_a_verdict_needs_more_than_a_handful_of_samples(self):
        """The script refuses to call a p95 over three rows a p95, because that is
        how a plan ends up citing a threshold nobody actually measured."""
        assert check_generation_latency.MIN_SAMPLES_FOR_A_VERDICT >= 20
