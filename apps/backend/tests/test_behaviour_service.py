"""Unit tests for behaviour_service pure computation functions (no DB required).

Covers the original helper arithmetic (bucketing, consistency, dropout risk, risk
factors) plus the timezone-correctness and evidence-gating work added when this
analysis was fixed.

Context for the newer cases: this analysis had never once completed in production.
`tasks/behaviour.py` called `analyze_behaviour(user_id=...)` while the signature
required a `sessions` argument, so every learner raised `TypeError` into a bare
`except` and every behaviour column stayed `NULL` — which meant the Plus "optimal
study times" feature returned `null` for every subscriber, and
`avg_session_minutes` silently fell back to 60 wherever it was read.

Two invariants the newer cases guard:

1. **Nothing unmeasured is reported as zero.** A learner who has not practised has
   no average session length, and writing `0` there feeds a zero daily budget into
   study-plan generation.
2. **No claim about a learner's day without their timezone.** Hours only mean
   something on their own wall clock, so the distribution records whether it is
   `local` or `utc_assumed`, and the consumers that turn it into a sentence refuse
   the assumed case.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta

from src.domains.personal_learning.services.behaviour_service import (
    MIN_SESSIONS_FOR_TIME_PATTERN,
    PracticeSession,
    _compute_consistency_score,
    _compute_dropout_risk,
    _compute_optimal_times,
    _compute_predictive_scheduling,
    _compute_preferred_times,
    _compute_risk_factors,
    compute_behaviour,
)
from src.shared.time import UNKNOWN_TIMEZONE
from src.shared.time.learner_timezone import _from_parts

LAGOS = _from_parts("Africa/Lagos", "DEVICE")  # UTC+1
UTC_KNOWN = _from_parts("UTC", "MANUAL")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _session(*, hours_ago: float = 0, minutes: float | None = 30) -> PracticeSession:
    """A session relative to the real current time, for the risk helpers."""
    return PracticeSession(
        started_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        duration_minutes=minutes,
    )


def _days_ago(days: float, *, minutes: float | None = 30.0) -> PracticeSession:
    return PracticeSession(
        started_at=datetime.now(UTC) - timedelta(days=days),
        duration_minutes=minutes,
    )


def _at(hour: int, *, day: int = 11, minutes: float | None = 30) -> PracticeSession:
    """A session at a fixed UTC hour, for the deterministic bucketing cases."""
    return PracticeSession(
        started_at=datetime(2026, 8, day, hour, 0, tzinfo=UTC),
        duration_minutes=minutes,
    )


def _local(*hours_ago: float) -> list[datetime]:
    """Local datetimes for the consistency helper, newest offsets first."""
    now = datetime.now(UTC)
    return [now - timedelta(hours=h) for h in hours_ago]


# ---------------------------------------------------------------------------
# _compute_preferred_times
# ---------------------------------------------------------------------------


class TestComputePreferredTimes:
    """Bucketing itself. Percentages live under `buckets`; see the nesting test."""

    def _buckets(self, hours, timezone_=UTC_KNOWN):
        return _compute_preferred_times(hours, timezone_, len(hours))["buckets"]

    def test_empty_hours(self):
        assert self._buckets([]) == {
            "morning": 0.0,
            "afternoon": 0.0,
            "evening": 0.0,
            "night": 0.0,
        }

    def test_all_morning(self):
        result = self._buckets([6, 7, 8, 9, 10, 11])
        assert result["morning"] == 100.0
        assert result["afternoon"] == 0.0
        assert result["evening"] == 0.0
        assert result["night"] == 0.0

    def test_all_afternoon(self):
        assert self._buckets([12, 13, 14, 15, 16])["afternoon"] == 100.0

    def test_all_evening(self):
        assert self._buckets([17, 18, 19, 20])["evening"] == 100.0

    def test_all_night(self):
        assert self._buckets([0, 1, 2, 3, 4, 21, 22, 23])["night"] == 100.0

    def test_mixed_distribution(self):
        # 2 morning, 2 afternoon, 1 evening, 1 night = 6 total
        result = self._buckets([8, 9, 13, 14, 18, 23])
        assert abs(result["morning"] - 33.3) < 0.1
        assert abs(result["afternoon"] - 33.3) < 0.1
        assert abs(result["evening"] - 16.7) < 0.1
        assert abs(result["night"] - 16.7) < 0.1

    def test_percentages_sum_to_100(self):
        result = self._buckets([5, 12, 17, 22, 8, 14, 19, 1])
        assert abs(sum(result.values()) - 100.0) < 0.5  # Allow small rounding

    def test_metadata_sits_outside_the_bucket_map(self):
        """The shape was flat, and consumers did `max(times, key=times.get)`.

        Any non-numeric value sharing that dict raises `TypeError` on comparison,
        so basis and counts have to live one level up.
        """
        result = _compute_preferred_times([8, 9, 10], UTC_KNOWN, 3)

        assert set(result["buckets"]) == {"morning", "afternoon", "evening", "night"}
        assert result["basis"] == "local"
        assert result["sessionCount"] == 3
        for value in result["buckets"].values():
            assert isinstance(value, float)

    def test_an_unknown_timezone_marks_the_basis_assumed(self):
        result = _compute_preferred_times([8, 9, 10], UNKNOWN_TIMEZONE, 3)

        assert result["basis"] == "utc_assumed"
        assert result["timezone"] is None

    def test_a_known_timezone_is_named(self):
        result = _compute_preferred_times([8], LAGOS, 1)

        assert result["basis"] == "local"
        assert result["timezone"] == "Africa/Lagos"


# ---------------------------------------------------------------------------
# _compute_consistency_score
# ---------------------------------------------------------------------------


class TestComputeConsistencyScore:
    def test_empty_sessions(self):
        assert _compute_consistency_score([]) == 0.0

    def test_single_session_today(self):
        # 1 day out of 1 day = 100
        assert _compute_consistency_score(_local(0)) == 100.0

    def test_sessions_every_day_for_30_days(self):
        times = _local(*[i * 24 for i in range(30)])
        assert _compute_consistency_score(times) == 100.0

    def test_sessions_every_other_day(self):
        times = _local(*[i * 24 for i in range(0, 30, 2)])  # 15 across 30 days
        assert 45.0 <= _compute_consistency_score(times) <= 55.0

    def test_old_sessions_excluded(self):
        """Consistency is defined over a period.

        A caller handing in sessions older than the window must not get a score
        that silently describes a different period than the one named.
        """
        times = _local(*[(40 + i) * 24 for i in range(10)])
        assert _compute_consistency_score(times) == 0.0

    def test_score_capped_at_100(self):
        # Multiple sessions on the same day do not push above 100
        assert _compute_consistency_score(_local(0, 2)) <= 100.0

    def test_counts_distinct_days_not_sessions(self):
        assert _compute_consistency_score(_local(1, 3, 5, 7, 9)) == 100.0

    def test_measured_from_the_first_session_not_the_whole_window(self):
        """A learner who joined four days ago has not missed twenty-six days."""
        times = _local(0, 24, 48, 72)
        assert _compute_consistency_score(times) == 100.0


# ---------------------------------------------------------------------------
# _compute_dropout_risk
# ---------------------------------------------------------------------------


class TestComputeDropoutRisk:
    def test_fewer_than_3_sessions(self):
        assert _compute_dropout_risk([_days_ago(1), _days_ago(0)]) == 0.0

    def test_no_risk_consistent_sessions(self):
        sessions = [_days_ago(i) for i in range(5)]
        # Consistent duration and even gaps — should be low risk
        assert _compute_dropout_risk(sessions) <= 0.2

    def test_declining_durations_increase_risk(self):
        sessions = [
            _days_ago(4, minutes=60.0),
            _days_ago(3, minutes=55.0),
            _days_ago(2, minutes=20.0),
            _days_ago(1, minutes=15.0),
            _days_ago(0, minutes=10.0),
        ]
        assert _compute_dropout_risk(sessions) >= 0.4

    def test_growing_gaps_increase_risk(self):
        # Gaps grow: 1h, 2h, 12h, 24h, 70h
        sessions = [
            _session(hours_ago=109),
            _session(hours_ago=108),
            _session(hours_ago=106),
            _session(hours_ago=94),
            _session(hours_ago=70),
            _session(hours_ago=0),
        ]
        assert _compute_dropout_risk(sessions) >= 0.4

    def test_no_recent_session_adds_risk(self):
        # All sessions 5+ days ago — at least the inactivity factor
        sessions = [_days_ago(7), _days_ago(6), _days_ago(5)]
        assert _compute_dropout_risk(sessions) >= 0.2

    def test_risk_capped_at_1(self):
        sessions = [
            _days_ago(30, minutes=120.0),
            _days_ago(25, minutes=100.0),
            _days_ago(15, minutes=40.0),
            _days_ago(5, minutes=10.0),
            _days_ago(4, minutes=5.0),
        ]
        assert _compute_dropout_risk(sessions) <= 1.0

    def test_a_session_with_no_reported_duration_is_not_a_decline(self):
        """A missing duration is unknown, not zero.

        Treating it as zero would read as a collapse in effort and inflate risk.
        """
        sessions = [
            _days_ago(4, minutes=60.0),
            _days_ago(3, minutes=None),
            _days_ago(2, minutes=None),
            _days_ago(1, minutes=60.0),
            _days_ago(0, minutes=60.0),
        ]
        assert _compute_dropout_risk(sessions) <= 0.2


# ---------------------------------------------------------------------------
# _compute_risk_factors
# ---------------------------------------------------------------------------


class TestComputeRiskFactors:
    def test_none_risk(self):
        assert _compute_risk_factors(None) is None

    def test_zero_risk(self):
        assert _compute_risk_factors(0.0) is None

    def test_low_risk_below_threshold(self):
        assert _compute_risk_factors(0.2) is None

    def test_moderate_risk(self):
        result = _compute_risk_factors(0.4)
        assert result is not None
        assert "declining_session_duration" in result

    def test_high_risk(self):
        result = _compute_risk_factors(0.6)
        assert result is not None
        assert "declining_session_duration" in result
        assert "growing_gaps_between_sessions" in result

    def test_very_high_risk(self):
        result = _compute_risk_factors(0.8)
        assert result is not None
        assert "declining_session_duration" in result
        assert "growing_gaps_between_sessions" in result
        assert "extended_inactivity" in result


# ---------------------------------------------------------------------------
# compute_behaviour — the whole metric set
# ---------------------------------------------------------------------------


class TestNothingMeasuredIsNotZero:
    def test_no_sessions_reports_null_everywhere(self):
        """Previously returned 0.0 for three of these.

        `avgSessionMinutes: 0.0` is the damaging one: study-plan generation reads
        it, and a zero average produces a zero daily minute budget.
        """
        result = compute_behaviour([], LAGOS)

        assert result["preferredStudyTimes"] is None
        assert result["avgSessionMinutes"] is None
        assert result["consistencyScore"] is None
        assert result["bestDayOfWeek"] is None
        assert result["dropoutRisk"] is None

    def test_sessions_without_reported_duration_leave_the_average_null(self):
        """A session whose duration the client never sent is not a 0-minute one."""
        sessions = [_at(9, minutes=None), _at(10, minutes=None), _at(11, minutes=None)]

        assert compute_behaviour(sessions, LAGOS)["avgSessionMinutes"] is None

    def test_the_average_uses_only_sessions_that_reported_one(self):
        sessions = [_at(9, minutes=20), _at(10, minutes=None), _at(11, minutes=40)]

        assert compute_behaviour(sessions, LAGOS)["avgSessionMinutes"] == 30.0


class TestTimeOfDayNeedsEvidence:
    def test_below_the_threshold_no_pattern_is_claimed(self):
        """Four buckets over a sparse history routinely leave three empty.

        "You study best in the morning" from two sessions is an accident of when
        the learner happened to start, not a pattern.
        """
        sessions = [_at(9), _at(10)]
        assert len(sessions) < MIN_SESSIONS_FOR_TIME_PATTERN

        result = compute_behaviour(sessions, LAGOS)

        assert result["preferredStudyTimes"] is None
        assert result["bestDayOfWeek"] is None

    def test_at_the_threshold_a_distribution_is_recorded(self):
        sessions = [_at(9, day=d) for d in range(1, 6)]

        result = compute_behaviour(sessions, LAGOS)

        assert result["preferredStudyTimes"] is not None
        assert result["preferredStudyTimes"]["sessionCount"] == 5


class TestBucketsAreLocal:
    def test_hours_are_bucketed_on_the_learners_clock(self):
        """4am UTC is 5am in Lagos, which crosses `night` into `morning`."""
        sessions = [_at(4, day=d) for d in range(1, 6)]

        lagos = compute_behaviour(sessions, LAGOS)["preferredStudyTimes"]
        utc = compute_behaviour(sessions, UTC_KNOWN)["preferredStudyTimes"]

        assert lagos["buckets"]["morning"] == 100.0
        assert utc["buckets"]["night"] == 100.0

    def test_best_day_is_withheld_when_the_timezone_is_unknown(self):
        """A session near midnight falls on a different weekday by zone."""
        sessions = [_at(23, day=d) for d in range(1, 6)]

        assert compute_behaviour(sessions, UNKNOWN_TIMEZONE)["bestDayOfWeek"] is None
        assert compute_behaviour(sessions, LAGOS)["bestDayOfWeek"] is not None

    def test_best_day_uses_the_local_weekday(self):
        """23:00 UTC on 11 Aug 2026 (Tuesday) is 00:00 on the 12th in Lagos."""
        sessions = [_at(23, day=11) for _ in range(5)]

        assert compute_behaviour(sessions, UTC_KNOWN)["bestDayOfWeek"] == "Tuesday"
        assert compute_behaviour(sessions, LAGOS)["bestDayOfWeek"] == "Wednesday"


class TestPlusConsumersRefuseAssumedHours:
    """The live wrong-claim guard.

    These two functions turn the distribution into a sentence shown to a Plus
    subscriber. Without a captured timezone the underlying hours are UTC, so the
    named slot is wrong for most of the world.
    """

    def _profile(self, preferred_times, *, avg=45.0, consistency=80.0):
        return {
            "preferredTimes": preferred_times,
            "avgSessionMinutes": avg,
            "consistencyScore": consistency,
        }

    def _times(self, buckets, *, basis="local", timezone="UTC"):
        return {
            "buckets": buckets,
            "basis": basis,
            "timezone": timezone,
            "sessionCount": 9,
        }

    def test_optimal_times_refuses_an_assumed_basis(self):
        assumed = self._times(
            {"morning": 80.0, "afternoon": 20.0, "evening": 0.0, "night": 0.0},
            basis="utc_assumed",
            timezone=None,
        )

        assert _compute_optimal_times(self._profile(assumed)) is None

    def test_optimal_times_answers_on_a_local_basis(self):
        local = self._times(
            {"morning": 10.0, "afternoon": 20.0, "evening": 70.0, "night": 0.0},
            timezone="Africa/Lagos",
        )

        result = _compute_optimal_times(self._profile(local))

        assert result["primarySlot"] == "evening"
        assert result["primaryPercentage"] == 70.0
        assert result["secondarySlot"] == "afternoon"

    def test_the_copy_claims_frequency_not_performance(self):
        """It measures where practice volume falls, which is not where the learner
        performs best. Saying "you learn best" from a frequency distribution
        asserts something that was never measured."""
        local = self._times({"morning": 60.0, "afternoon": 40.0, "evening": 0.0, "night": 0.0})

        recommendation = _compute_optimal_times(self._profile(local))["recommendation"]

        assert "learn best" not in recommendation
        assert "practice happens" in recommendation

    def test_predictive_scheduling_refuses_an_assumed_basis(self):
        assumed = self._times(
            {"morning": 100.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0},
            basis="utc_assumed",
            timezone=None,
        )

        assert _compute_predictive_scheduling(self._profile(assumed)) is None

    def test_predictive_scheduling_refuses_without_a_measured_session_length(self):
        """It stretches what the learner has sustained. With nothing sustained
        there is nothing to stretch, and the old default of 45 minutes described
        someone nobody had observed."""
        local = self._times({"morning": 100.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0})

        assert _compute_predictive_scheduling(self._profile(local, avg=None)) is None

    def test_predictive_scheduling_stretches_the_observed_length(self):
        local = self._times({"morning": 100.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0})

        result = _compute_predictive_scheduling(self._profile(local, avg=40.0))

        assert result["suggestedSlot"] == "morning"
        assert result["suggestedDurationMinutes"] == 44
        assert result["consistencyTrend"] == "improving"

    def test_a_legacy_flat_shape_is_not_treated_as_local(self):
        """Anything cached before nesting has no basis recorded, so it cannot be
        shown to be the learner's own clock and must not be claimed."""
        legacy = {"morning": 80.0, "afternoon": 20.0, "evening": 0.0, "night": 0.0}

        assert _compute_optimal_times(self._profile(legacy)) is None
        assert _compute_predictive_scheduling(self._profile(legacy)) is None

    def test_missing_or_malformed_values_are_refused(self):
        for value in (None, {}, "morning", {"basis": "local"}, {"basis": "local", "buckets": {}}):
            assert _compute_optimal_times(self._profile(value)) is None
