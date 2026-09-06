"""Per-type outcome attribution rules.

The intelligence layer optimises for the label these rules define, so the label has to be right
before the ranker matters. Two things are pinned here: that "success" means the meaningful outcome
within the type's own window rather than a mere open, and that a type with nothing to act on is not
silently held to an impossible standard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import get_args

from src.domains.notifications.models import InteractionEvent
from src.domains.notifications.taxonomy import (
    _POSITIVE_EVENTS,
    NOTIFICATION_SPECS,
    notification_spec,
)

DELIVERED = datetime(2026, 9, 3, 12, tzinfo=UTC)


class TestCountsAsSuccess:
    def test_the_success_event_within_the_window_counts(self) -> None:
        spec = notification_spec("learning.study_session_reminder")  # success ACTIONED, 2h window
        assert spec.counts_as_success(
            "ACTIONED", delivered_at=DELIVERED, occurred_at=DELIVERED + timedelta(minutes=30)
        )

    def test_the_same_event_past_the_window_does_not_count(self) -> None:
        spec = notification_spec("learning.study_session_reminder")
        assert not spec.counts_as_success(
            "ACTIONED", delivered_at=DELIVERED, occurred_at=DELIVERED + timedelta(hours=5)
        )

    def test_an_open_is_not_success_for_an_action_type(self) -> None:
        # The plan's rule: an open is not automatically success.
        spec = notification_spec("learning.study_session_reminder")
        assert not spec.counts_as_success(
            "OPENED", delivered_at=DELIVERED, occurred_at=DELIVERED + timedelta(minutes=1)
        )

    def test_a_missing_timestamp_is_not_success(self) -> None:
        spec = notification_spec("learning.study_session_reminder")
        assert not spec.counts_as_success("ACTIONED", delivered_at=None, occurred_at=DELIVERED)


class TestPerTypeDefaults:
    def test_a_type_with_no_action_is_scored_on_opening(self) -> None:
        # A digest cannot be "acted on" — opening it is the outcome we can attribute.
        for name in ("learning.digest", "progress.digest", "social.digest"):
            assert notification_spec(name).success_events == ("OPENED",), name

    def test_an_action_type_is_scored_on_doing_the_action(self) -> None:
        assert notification_spec("learning.review_due").success_events == ("ACTIONED",)

    def test_every_type_has_a_positive_window_and_positive_events(self) -> None:
        for name, spec in NOTIFICATION_SPECS.items():
            assert spec.attribution_window > timedelta(0), name
            assert spec.success_events, name
            assert set(spec.success_events).issubset(_POSITIVE_EVENTS), name

    def test_the_window_never_outlives_the_notification_when_bounded(self) -> None:
        # A short-lived reminder cannot be credited with an action after it has expired.
        spec = notification_spec("learning.study_session_reminder")  # ttl 2h
        assert spec.attribution_window <= timedelta(hours=2)


class TestVocabularyStaysInSync:
    def test_positive_events_are_a_subset_of_the_model_enum(self) -> None:
        # The taxonomy keeps its own copy to avoid an import cycle; this guards the drift.
        all_events = set(get_args(InteractionEvent))
        assert _POSITIVE_EVENTS.issubset(all_events)
        # The negative signals must not be counted as positive.
        assert _POSITIVE_EVENTS.isdisjoint({"DISMISSED", "UNSUBSCRIBED", "DECLINED", "SNOOZED"})
