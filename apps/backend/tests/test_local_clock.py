"""Quiet hours and day boundaries, on the learner's clock rather than Greenwich's.

**The defect these exist for.** `notification_service` used to call `dt.time()` on an aware UTC instant,
discarding the offset, and compare Greenwich's wall clock against the learner's stated `"22:00"`. Meanwhile
`agenda_service` had a second, correct implementation. So the app declined to plan a session at 23:00 in a
learner's evening and then messaged them during it — and for a learner in Los Angeles the broken version
treated 7pm as quiet, because 02:00 UTC is inside a 22:00–07:00 window read as UTC.

The same root cause runs through the daily notification allowance: a cap counted over a UTC calendar day
refills at 01:00 for a learner in Lagos and 16:00 for one in Los Angeles, so the second could be messaged
their whole quota twice inside a working day.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, time  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from src.shared.time import (  # noqa: E402
    UNKNOWN_TIMEZONE,
    LearnerTimezone,
    is_within_quiet_hours,
    local_day_bounds,
    next_end_of_quiet_hours,
    parse_hhmm,
)

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
)
LOS_ANGELES = LearnerTimezone(
    zone=ZoneInfo("America/Los_Angeles"),
    name="America/Los_Angeles",
    is_known=True,
    source="DEVICE",
)
NEW_YORK = LearnerTimezone(
    zone=ZoneInfo("America/New_York"), name="America/New_York", is_known=True, source="DEVICE"
)

QUIET_FROM, QUIET_TO = time(22, 0), time(7, 0)


class TestParsing:
    def test_a_stored_window_becomes_times(self):
        assert parse_hhmm("22:00") == time(22, 0)
        assert parse_hhmm("07:30") == time(7, 30)

    def test_unusable_values_read_as_no_quiet_hours(self):
        for value in (None, "", "bad", "22", "99:99", ":"):
            assert parse_hhmm(value) is None, value

    def test_it_fails_open_rather_than_silencing_a_learner_forever(self):
        """The uncomfortable direction, chosen deliberately. A corrupt value means a message at a bad hour,
        which is visible and complainable; the alternative is one bad string silencing every notification for
        that learner with nothing reporting it."""
        assert (
            is_within_quiet_hours(
                datetime(2026, 8, 27, 23, 0, tzinfo=UTC), LAGOS, parse_hhmm("garbage"), QUIET_TO
            )
            is False
        )


class TestQuietHoursAreLocal:
    def test_the_learners_evening_is_quiet_not_greenwichs(self):
        """23:00 in Lagos is 22:00 UTC. Quiet either way — which is the coincidence that hid this bug."""
        assert is_within_quiet_hours(
            datetime(2026, 8, 27, 22, 0, tzinfo=UTC), LAGOS, QUIET_FROM, QUIET_TO
        )

    def test_a_seven_thirty_message_is_no_longer_treated_as_night(self):
        """06:30 UTC is 07:30 in Lagos — past the end of their quiet hours. Read as UTC it fell inside the
        window, so this learner's morning notifications were held back an hour."""
        instant = datetime(2026, 8, 27, 6, 30, tzinfo=UTC)
        assert is_within_quiet_hours(instant, LAGOS, QUIET_FROM, QUIET_TO) is False
        # What the old UTC-clock comparison concluded, for contrast.
        assert instant.time() <= QUIET_TO

    def test_seven_in_the_evening_is_not_quiet_in_los_angeles(self):
        """The clearest case. 02:00 UTC is 19:00 the previous day in Los Angeles — the middle of their
        evening. Compared as a UTC clock it sat inside 22:00–07:00, so the learner most likely to be
        studying was the one guaranteed not to hear from us."""
        instant = datetime(2026, 8, 27, 2, 0, tzinfo=UTC)
        assert is_within_quiet_hours(instant, LOS_ANGELES, QUIET_FROM, QUIET_TO) is False
        assert instant.time() <= QUIET_TO

    def test_ten_at_night_in_los_angeles_is_quiet(self):
        assert is_within_quiet_hours(
            datetime(2026, 8, 28, 5, 0, tzinfo=UTC), LOS_ANGELES, QUIET_FROM, QUIET_TO
        )

    def test_an_unknown_timezone_reads_utc(self):
        """No worse than the behaviour being replaced, and now conditional on something a caller can check
        rather than silently assumed."""
        assert is_within_quiet_hours(
            datetime(2026, 8, 27, 23, 0, tzinfo=UTC), UNKNOWN_TIMEZONE, QUIET_FROM, QUIET_TO
        )


class TestTheWindowShape:
    def test_the_start_is_quiet_and_the_end_is_not(self):
        """Half-open. With both bounds inclusive there is no instant at which the boundary minute is
        available, so a window ending at 07:00 was still quiet at 07:00."""
        at_start = datetime(2026, 8, 27, 21, 0, tzinfo=UTC)  # 22:00 Lagos
        at_end = datetime(2026, 8, 27, 6, 0, tzinfo=UTC)  # 07:00 Lagos
        assert is_within_quiet_hours(at_start, LAGOS, QUIET_FROM, QUIET_TO) is True
        assert is_within_quiet_hours(at_end, LAGOS, QUIET_FROM, QUIET_TO) is False

    def test_a_window_crossing_midnight_covers_both_sides(self):
        before = datetime(2026, 8, 27, 22, 30, tzinfo=UTC)  # 23:30 Lagos
        after = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)  # 05:00 Lagos
        assert is_within_quiet_hours(before, LAGOS, QUIET_FROM, QUIET_TO)
        assert is_within_quiet_hours(after, LAGOS, QUIET_FROM, QUIET_TO)

    def test_a_same_day_window_does_not_wrap(self):
        midday = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)  # 12:00 Lagos
        evening = datetime(2026, 8, 27, 19, 0, tzinfo=UTC)  # 20:00 Lagos
        assert is_within_quiet_hours(midday, LAGOS, time(9, 0), time(17, 0))
        assert is_within_quiet_hours(evening, LAGOS, time(9, 0), time(17, 0)) is False

    def test_a_same_day_window_is_half_open_at_both_ends(self):
        """The same rule as the overnight case, asserted on the other branch. Two branches with different
        boundary behaviour would mean a learner's 17:00 being available or not depending on whether their
        quiet hours happened to cross midnight."""
        at_start = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)  # 09:00 Lagos
        at_end = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)  # 17:00 Lagos
        assert is_within_quiet_hours(at_start, LAGOS, time(9, 0), time(17, 0)) is True
        assert is_within_quiet_hours(at_end, LAGOS, time(9, 0), time(17, 0)) is False

    def test_the_overnight_window_ends_exactly_on_its_end(self):
        """Exercises the wrapping branch's own boundary. 06:00 UTC is 07:00 in Lagos, the minute quiet hours
        end, and it must be available."""
        assert (
            is_within_quiet_hours(
                datetime(2026, 8, 27, 6, 0, tzinfo=UTC), LAGOS, time(22, 0), time(7, 0)
            )
            is False
        )
        assert (
            is_within_quiet_hours(
                datetime(2026, 8, 27, 5, 59, tzinfo=UTC), LAGOS, time(22, 0), time(7, 0)
            )
            is True
        )

    def test_half_a_window_is_not_a_window(self):
        instant = datetime(2026, 8, 27, 23, 0, tzinfo=UTC)
        assert is_within_quiet_hours(instant, LAGOS, QUIET_FROM, None) is False
        assert is_within_quiet_hours(instant, LAGOS, None, QUIET_TO) is False

    def test_the_agenda_reads_the_same_function(self):
        """Not a copy — the same object, so the two surfaces cannot drift apart again."""
        from src.domains.progress.services import agenda_service

        assert agenda_service._within_quiet_hours is is_within_quiet_hours


class TestWhenQuietHoursEnd:
    def test_it_returns_the_learners_morning_as_a_utc_instant(self):
        """23:00 Lagos, quiet until 07:00 Lagos, which is 06:00 UTC."""
        instant = datetime(2026, 8, 27, 22, 0, tzinfo=UTC)  # 23:00 Lagos
        assert next_end_of_quiet_hours(instant, LAGOS, QUIET_TO) == datetime(
            2026, 8, 28, 6, 0, tzinfo=UTC
        )

    def test_an_end_already_past_today_means_tomorrow(self):
        instant = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)  # 13:00 Lagos, past 07:00
        assert next_end_of_quiet_hours(instant, LAGOS, QUIET_TO) == datetime(
            2026, 8, 28, 6, 0, tzinfo=UTC
        )

    def test_no_end_means_no_waiting(self):
        instant = datetime(2026, 8, 27, 22, 0, tzinfo=UTC)
        assert next_end_of_quiet_hours(instant, LAGOS, None) == instant

    def test_it_survives_a_daylight_saving_transition(self):
        """New York springs forward on 8 March 2026. An instant at 22:00 local on the 7th is 03:00 UTC on
        the 8th; quiet hours end at 07:00 local, which is 11:00 UTC that day because the clocks moved. Adding
        a fixed number of hours to the UTC instant would land at 12:00 — an hour late, once a year, in a way
        nobody would trace back to here."""
        instant = datetime(2026, 3, 8, 3, 0, tzinfo=UTC)
        assert next_end_of_quiet_hours(instant, NEW_YORK, QUIET_TO) == datetime(
            2026, 3, 8, 11, 0, tzinfo=UTC
        )


class TestTheLearnersOwnDay:
    def test_the_day_brackets_their_midnight_not_greenwichs(self):
        """Lagos is an hour ahead, so their day starts at 23:00 UTC the evening before."""
        since, until = local_day_bounds(datetime(2026, 8, 27, 12, 0, tzinfo=UTC), LAGOS)
        assert since == datetime(2026, 8, 26, 23, 0, tzinfo=UTC)
        assert until == datetime(2026, 8, 27, 23, 0, tzinfo=UTC)

    def test_a_learner_behind_utc_gets_their_own_window(self):
        since, until = local_day_bounds(datetime(2026, 8, 27, 12, 0, tzinfo=UTC), LOS_ANGELES)
        assert since == datetime(2026, 8, 27, 7, 0, tzinfo=UTC)
        assert until == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)

    def test_the_window_is_half_open_so_days_neither_overlap_nor_gap(self):
        _, until = local_day_bounds(datetime(2026, 8, 27, 12, 0, tzinfo=UTC), LAGOS)
        next_since, _ = local_day_bounds(datetime(2026, 8, 28, 12, 0, tzinfo=UTC), LAGOS)
        assert until == next_since

    def test_a_day_containing_a_clock_change_is_still_one_local_day(self):
        """23 hours of UTC, not 24. Truncating in UTC and converting afterwards would give Greenwich's
        midnight expressed in New York, which is not the learner's midnight."""
        since, until = local_day_bounds(datetime(2026, 3, 8, 18, 0, tzinfo=UTC), NEW_YORK)
        assert since == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
        assert until == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)
        assert (until - since).total_seconds() == 23 * 3600
