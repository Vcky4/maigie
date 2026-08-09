"""Unit tests for notification service pure logic (no DB required)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timezone

import pytest

from src.domains.personal_learning.services.notification_service import (
    _is_during_quiet_hours,
    _reschedule_after_quiet_hours,
)

# ---------------------------------------------------------------------------
# TestQuietHours
# ---------------------------------------------------------------------------


class TestQuietHours:
    """Tests for _is_during_quiet_hours pure logic."""

    def test_no_quiet_hours_configured(self):
        """Returns False when start/end are None."""
        dt = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, None, None) is False

    def test_within_overnight_quiet_hours(self):
        """22:00-07:00, check 23:30 → True."""
        dt = datetime(2025, 1, 15, 23, 30, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is True

    def test_within_overnight_quiet_hours_early_morning(self):
        """22:00-07:00, check 05:00 → True."""
        dt = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is True

    def test_outside_overnight_quiet_hours(self):
        """22:00-07:00, check 12:00 → False."""
        dt = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is False

    def test_outside_overnight_quiet_hours_afternoon(self):
        """22:00-07:00, check 15:00 → False."""
        dt = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is False

    def test_same_day_quiet_hours_inside(self):
        """09:00-17:00, check 12:00 → True."""
        dt = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "09:00", "17:00") is True

    def test_same_day_quiet_hours_outside(self):
        """09:00-17:00, check 20:00 → False."""
        dt = datetime(2025, 1, 15, 20, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "09:00", "17:00") is False

    def test_boundary_start(self):
        """Exactly at start time → True."""
        dt = datetime(2025, 1, 15, 22, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is True

    def test_boundary_end(self):
        """Exactly at end time → True."""
        dt = datetime(2025, 1, 15, 7, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "22:00", "07:00") is True

    def test_invalid_format(self):
        """Returns False for malformed time strings."""
        dt = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        assert _is_during_quiet_hours(dt, "not-a-time", "07:00") is False
        assert _is_during_quiet_hours(dt, "22:00", "bad") is False
        assert _is_during_quiet_hours(dt, "", "") is False


# ---------------------------------------------------------------------------
# TestRescheduleAfterQuietHours
# ---------------------------------------------------------------------------


class TestRescheduleAfterQuietHours:
    """Tests for _reschedule_after_quiet_hours pure logic."""

    def test_reschedules_to_next_morning(self):
        """Quiet ends at 07:00, notification at 23:00 → reschedules to 07:00 next day."""
        dt = datetime(2025, 1, 15, 23, 0, tzinfo=UTC)
        result = _reschedule_after_quiet_hours(dt, "07:00")
        expected = datetime(2025, 1, 16, 7, 0, tzinfo=UTC)
        assert result == expected

    def test_reschedules_same_day(self):
        """Quiet ends at 17:00, notification at 10:00 → reschedules to 17:00 same day."""
        dt = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        result = _reschedule_after_quiet_hours(dt, "17:00")
        expected = datetime(2025, 1, 15, 17, 0, tzinfo=UTC)
        assert result == expected

    def test_no_end_configured(self):
        """Returns original datetime unchanged when end is None."""
        dt = datetime(2025, 1, 15, 23, 0, tzinfo=UTC)
        result = _reschedule_after_quiet_hours(dt, None)
        assert result == dt

    def test_invalid_end_format(self):
        """Returns original datetime unchanged for malformed end string."""
        dt = datetime(2025, 1, 15, 23, 0, tzinfo=UTC)
        assert _reschedule_after_quiet_hours(dt, "bad") == dt
        assert _reschedule_after_quiet_hours(dt, "") == dt
