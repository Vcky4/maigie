"""Tests for learner timezone resolution (no DB required).

Every timestamp in the database is a UTC instant. Several things the product wants
to say are claims about a learner's *local* wall clock, and converting requires
knowing where they are. The trap this guards is that
``UserPreferences.timezone`` is ``NOT NULL`` defaulting to ``"UTC"`` and predates
anything asking for it, so reading it naively makes every learner who has never
been asked look like a learner in London.

The invariant under test: a timezone is only *known* when something actually
captured it. Everything else resolves to unknown, and unknown is usable as a
fallback but must not be presented as a fact.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime

from src.shared.time import UNKNOWN_TIMEZONE, local_hour, to_learner_local
from src.shared.time.learner_timezone import _from_parts

# A learner east of UTC, so a late-evening UTC instant is already tomorrow.
LAGOS = _from_parts("Africa/Lagos", "DEVICE")
# And one west of it, so an early-morning UTC instant is still yesterday.
NEW_YORK = _from_parts("America/New_York", "MANUAL")


class TestKnownVersusAssumed:
    """The distinction the whole module exists for."""

    def test_a_zone_without_a_source_is_not_known(self):
        """This is every pre-existing row: the value is a default, not a fact."""
        assert _from_parts("Europe/London", None).is_known is False

    def test_a_device_reported_zone_is_known(self):
        assert _from_parts("Europe/London", "DEVICE").is_known is True

    def test_a_learner_stated_zone_is_known(self):
        assert _from_parts("Africa/Lagos", "MANUAL").is_known is True

    def test_a_genuine_utc_learner_is_still_known(self):
        """UTC is a real timezone.

        The ambiguity is in the *absence of a source*, not in the value, so a
        learner who really is in UTC must not be treated as unmeasured.
        """
        resolved = _from_parts("UTC", "DEVICE")
        assert resolved.is_known is True
        assert resolved.name == "UTC"

    def test_an_unparseable_zone_is_unknown_rather_than_raising(self):
        """A corrupt or retired IANA name must not break a read for that learner."""
        assert _from_parts("Not/AZone", "DEVICE").is_known is False

    def test_an_unrecognised_source_is_not_trusted(self):
        """Only DEVICE and MANUAL count. An unexpected value is not evidence."""
        assert _from_parts("Africa/Lagos", "GUESSED").is_known is False

    def test_an_empty_zone_is_unknown(self):
        assert _from_parts("", "DEVICE").is_known is False
        assert _from_parts(None, "DEVICE").is_known is False

    def test_is_assumed_is_the_inverse_of_is_known(self):
        assert LAGOS.is_assumed is False
        assert UNKNOWN_TIMEZONE.is_assumed is True

    def test_the_unknown_fallback_is_usable_but_flagged(self):
        """Callers get a working zone so display code needs no special case."""
        assert UNKNOWN_TIMEZONE.name == "UTC"
        assert UNKNOWN_TIMEZONE.is_known is False
        assert UNKNOWN_TIMEZONE.source is None


class TestLocalConversion:
    """Where the UTC-as-local bugs actually bite."""

    def test_a_late_utc_instant_is_already_tomorrow_east_of_utc(self):
        """23:30 UTC is 00:30 the next day in Lagos.

        Bucketed as a UTC hour this reads as a late-night session; in the
        learner's own day it is the small hours of the following morning. The
        difference changes both the hour bucket and the calendar day.
        """
        instant = datetime(2026, 8, 11, 23, 30, tzinfo=UTC)
        assert local_hour(instant, LAGOS) == 0
        assert to_learner_local(instant, LAGOS).day == 12

    def test_an_early_utc_instant_is_still_yesterday_west_of_utc(self):
        instant = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
        assert local_hour(instant, NEW_YORK) == 22
        assert to_learner_local(instant, NEW_YORK).day == 10

    def test_an_unknown_zone_falls_back_to_the_utc_hour(self):
        """Honest fallback: the hour is UTC's, and `is_known` says not to claim it."""
        instant = datetime(2026, 8, 11, 23, 30, tzinfo=UTC)
        assert local_hour(instant, UNKNOWN_TIMEZONE) == 23

    def test_a_naive_instant_is_read_as_utc(self):
        """Matches how these columns are written.

        Reading a naive value as *local* instead would silently shift every
        legacy row by the learner's offset.
        """
        naive = datetime(2026, 8, 11, 23, 30)
        assert to_learner_local(naive, LAGOS).hour == 0

    def test_conversion_preserves_the_instant(self):
        """Only the representation changes, never the moment."""
        instant = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
        assert to_learner_local(instant, LAGOS).timestamp() == instant.timestamp()

    def test_daylight_saving_is_handled_by_using_iana_names(self):
        """An offset would be wrong half the year; a zone name is not.

        New York is UTC-4 in August and UTC-5 in January. Storing "-04:00" would
        make every winter reading an hour out.
        """
        summer = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)
        winter = datetime(2026, 1, 11, 16, 0, tzinfo=UTC)
        assert local_hour(summer, NEW_YORK) == 12
        assert local_hour(winter, NEW_YORK) == 11
