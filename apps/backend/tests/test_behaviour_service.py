"""Unit tests for behaviour_service pure computation functions (no DB required)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from src.domains.personal_learning.services.behaviour_service import (
    _compute_consistency_score,
    _compute_dropout_risk,
    _compute_preferred_times,
    _compute_risk_factors,
)


# ---------------------------------------------------------------------------
# Helper: minimal session dataclass that mimics StudySession attributes
# ---------------------------------------------------------------------------


@dataclass
class FakeSession:
    start_time: datetime
    duration: float
    end_time: datetime | None = None


def _utc(year=2025, month=1, day=15, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _compute_preferred_times
# ---------------------------------------------------------------------------


class TestComputePreferredTimes:
    def test_empty_hours(self):
        result = _compute_preferred_times([])
        assert result == {"morning": 0.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0}

    def test_all_morning(self):
        result = _compute_preferred_times([6, 7, 8, 9, 10, 11])
        assert result["morning"] == 100.0
        assert result["afternoon"] == 0.0
        assert result["evening"] == 0.0
        assert result["night"] == 0.0

    def test_all_afternoon(self):
        result = _compute_preferred_times([12, 13, 14, 15, 16])
        assert result["afternoon"] == 100.0

    def test_all_evening(self):
        result = _compute_preferred_times([17, 18, 19, 20])
        assert result["evening"] == 100.0

    def test_all_night(self):
        result = _compute_preferred_times([0, 1, 2, 3, 4, 21, 22, 23])
        assert result["night"] == 100.0

    def test_mixed_distribution(self):
        # 2 morning, 2 afternoon, 1 evening, 1 night = 6 total
        result = _compute_preferred_times([8, 9, 13, 14, 18, 23])
        assert abs(result["morning"] - 33.3) < 0.1
        assert abs(result["afternoon"] - 33.3) < 0.1
        assert abs(result["evening"] - 16.7) < 0.1
        assert abs(result["night"] - 16.7) < 0.1

    def test_percentages_sum_to_100(self):
        result = _compute_preferred_times([5, 12, 17, 22, 8, 14, 19, 1])
        total = sum(result.values())
        assert abs(total - 100.0) < 0.5  # Allow small rounding


# ---------------------------------------------------------------------------
# _compute_consistency_score
# ---------------------------------------------------------------------------


class TestComputeConsistencyScore:
    def test_empty_sessions(self):
        assert _compute_consistency_score([]) == 0.0

    def test_single_session_today(self):
        now = datetime.now(timezone.utc)
        sessions = [FakeSession(start_time=now, duration=30.0)]
        score = _compute_consistency_score(sessions)
        # 1 day out of 1 day = 100
        assert score == 100.0

    def test_sessions_every_day_for_30_days(self):
        now = datetime.now(timezone.utc)
        sessions = [
            FakeSession(start_time=now - timedelta(days=i), duration=30.0) for i in range(30)
        ]
        score = _compute_consistency_score(sessions)
        assert score == 100.0

    def test_sessions_every_other_day(self):
        now = datetime.now(timezone.utc)
        sessions = [
            FakeSession(start_time=now - timedelta(days=i), duration=30.0)
            for i in range(0, 30, 2)  # 15 sessions over 30 days
        ]
        score = _compute_consistency_score(sessions)
        assert 45.0 <= score <= 55.0  # ~50%

    def test_old_sessions_excluded(self):
        now = datetime.now(timezone.utc)
        # All sessions are older than 30 days
        sessions = [
            FakeSession(start_time=now - timedelta(days=40 + i), duration=30.0) for i in range(10)
        ]
        score = _compute_consistency_score(sessions)
        assert score == 0.0

    def test_score_capped_at_100(self):
        now = datetime.now(timezone.utc)
        # Multiple sessions on same day don't push above 100
        sessions = [
            FakeSession(start_time=now, duration=30.0),
            FakeSession(start_time=now - timedelta(hours=2), duration=30.0),
        ]
        score = _compute_consistency_score(sessions)
        assert score <= 100.0


# ---------------------------------------------------------------------------
# _compute_dropout_risk
# ---------------------------------------------------------------------------


class TestComputeDropoutRisk:
    def test_fewer_than_3_sessions(self):
        now = datetime.now(timezone.utc)
        sessions = [
            FakeSession(start_time=now - timedelta(days=1), duration=30.0),
            FakeSession(start_time=now, duration=30.0),
        ]
        assert _compute_dropout_risk(sessions) == 0.0

    def test_no_risk_consistent_sessions(self):
        now = datetime.now(timezone.utc)
        sessions = [
            FakeSession(start_time=now - timedelta(days=i), duration=30.0) for i in range(5)
        ]
        risk = _compute_dropout_risk(sessions)
        # Consistent duration and even gaps — should be low risk
        assert risk <= 0.2

    def test_declining_durations_increase_risk(self):
        now = datetime.now(timezone.utc)
        # Durations go from 60 to 10 minutes
        sessions = [
            FakeSession(start_time=now - timedelta(days=4), duration=60.0),
            FakeSession(start_time=now - timedelta(days=3), duration=55.0),
            FakeSession(start_time=now - timedelta(days=2), duration=20.0),
            FakeSession(start_time=now - timedelta(days=1), duration=15.0),
            FakeSession(start_time=now, duration=10.0),
        ]
        risk = _compute_dropout_risk(sessions)
        assert risk >= 0.4  # Duration declining detected

    def test_growing_gaps_increase_risk(self):
        now = datetime.now(timezone.utc)
        # Gaps grow: 1h, 2h, 12h, 24h, 72h
        sessions = [
            FakeSession(start_time=now - timedelta(hours=109), duration=30.0),
            FakeSession(start_time=now - timedelta(hours=108), duration=30.0),
            FakeSession(start_time=now - timedelta(hours=106), duration=30.0),
            FakeSession(start_time=now - timedelta(hours=94), duration=30.0),
            FakeSession(start_time=now - timedelta(hours=70), duration=30.0),
            FakeSession(start_time=now, duration=30.0),
        ]
        risk = _compute_dropout_risk(sessions)
        # Growing gaps should add risk
        assert risk >= 0.4

    def test_no_recent_session_adds_risk(self):
        now = datetime.now(timezone.utc)
        # All sessions are 5+ days ago
        sessions = [
            FakeSession(start_time=now - timedelta(days=7), duration=30.0),
            FakeSession(start_time=now - timedelta(days=6), duration=30.0),
            FakeSession(start_time=now - timedelta(days=5), duration=30.0),
        ]
        risk = _compute_dropout_risk(sessions)
        assert risk >= 0.2  # At least the "no sessions in 3 days" factor

    def test_risk_capped_at_1(self):
        now = datetime.now(timezone.utc)
        # Extreme case: declining duration, growing gaps, no recent session
        sessions = [
            FakeSession(start_time=now - timedelta(days=30), duration=120.0),
            FakeSession(start_time=now - timedelta(days=25), duration=100.0),
            FakeSession(start_time=now - timedelta(days=15), duration=40.0),
            FakeSession(start_time=now - timedelta(days=5), duration=10.0),
            FakeSession(start_time=now - timedelta(days=4), duration=5.0),
        ]
        risk = _compute_dropout_risk(sessions)
        assert risk <= 1.0


# ---------------------------------------------------------------------------
# _compute_risk_factors
# ---------------------------------------------------------------------------


class TestComputeRiskFactors:
    def test_none_risk(self):
        assert _compute_risk_factors(None) is None

    def test_zero_risk(self):
        assert _compute_risk_factors(0.0) is None

    def test_low_risk_below_threshold(self):
        result = _compute_risk_factors(0.2)
        assert result is None

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
