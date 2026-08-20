"""Read-side derivations behind the flashcards page.

Everything here is pure: streaks, deck classification, activity grouping and the
insight ladder take rows in and return values out, with no database. That is
deliberate — these are the calculations that used to be fabricated by a fixture, so
they are the ones worth pinning down in tests that always run rather than in tests
that skip when no database is configured.

Query behaviour is covered in ``test_flashcard_repository.py`` against a real engine, and
route and ownership behaviour in ``test_flashcard_api.py``, which needs Postgres.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domains.personal_learning.repository import _streak_length
from src.domains.personal_learning.services import flashcard_service as fs
from src.shared.time.learner_timezone import UNKNOWN_TIMEZONE, LearnerTimezone

AUCKLAND = LearnerTimezone(
    zone=ZoneInfo("Pacific/Auckland"), name="Pacific/Auckland", is_known=True, source="DEVICE"
)
KNOWN_UTC = LearnerTimezone(zone=ZoneInfo("UTC"), name="UTC", is_known=True, source="MANUAL")


# ---------------------------------------------------------------------------
# Streaks
# ---------------------------------------------------------------------------


class TestStreakLength:
    def test_no_activity_is_no_streak(self):
        assert _streak_length(set(), date(2026, 8, 13)) == 0

    def test_counts_consecutive_days_ending_today(self):
        today = date(2026, 8, 13)
        active = {today, today - timedelta(days=1), today - timedelta(days=2)}
        assert _streak_length(active, today) == 3

    def test_survives_a_day_not_yet_started(self):
        """Not having reviewed yet this morning is not a broken streak."""
        today = date(2026, 8, 13)
        active = {today - timedelta(days=1), today - timedelta(days=2)}
        assert _streak_length(active, today) == 2

    def test_breaks_after_a_full_missed_day(self):
        today = date(2026, 8, 13)
        active = {today - timedelta(days=2), today - timedelta(days=3)}
        assert _streak_length(active, today) == 0

    def test_ignores_days_before_a_gap(self):
        today = date(2026, 8, 13)
        active = {
            today,
            today - timedelta(days=1),
            # gap at day 2
            today - timedelta(days=3),
            today - timedelta(days=4),
        }
        assert _streak_length(active, today) == 2

    def test_reviewing_again_today_does_not_erase_yesterday(self):
        """The regression this table was added for.

        The old derivation read one ``lastReviewedAt`` per card, so re-reviewing
        yesterday's cards today moved their only timestamp forward and yesterday
        disappeared from the observed set — the streak shrank because the learner
        studied. With per-review rows both days are present independently.
        """
        today = date(2026, 8, 13)
        yesterday = today - timedelta(days=1)
        # Three cards graded yesterday, the same three graded again today. A
        # per-card projection would yield {today} only.
        review_days = {yesterday, yesterday, yesterday, today, today, today}
        assert _streak_length(review_days, today) == 2


# ---------------------------------------------------------------------------
# Deck classification
# ---------------------------------------------------------------------------


class TestDeckStatus:
    def test_anything_due_outranks_maturity(self):
        """A fully mature deck with work waiting is still work waiting."""
        assert fs.deck_status(card_count=10, due_count=1, mastered_count=10) == "due"

    def test_strong_at_the_threshold(self):
        assert fs.deck_status(card_count=10, due_count=0, mastered_count=8) == "strong"

    def test_learning_below_the_threshold(self):
        assert fs.deck_status(card_count=10, due_count=0, mastered_count=7) == "learning"

    def test_empty_deck_is_learning_not_strong(self):
        """Zero of zero cards mature is not mastery."""
        assert fs.deck_status(card_count=0, due_count=0, mastered_count=0) == "learning"


class TestMasteryPercent:
    def test_empty_set_is_zero_not_an_error(self):
        assert fs.mastery_percent(0, 0) == 0

    def test_rounds_to_a_whole_percent(self):
        assert fs.mastery_percent(1, 3) == 33

    def test_clamped_to_a_hundred(self):
        assert fs.mastery_percent(12, 10) == 100


# ---------------------------------------------------------------------------
# Statistics shaping
# ---------------------------------------------------------------------------


def test_shape_statistics_publishes_every_field_it_computes():
    """The contract used to declare four fields while the service produced eight.

    The streak and weekly counts were computed on every request and dropped on the
    way out, so no client could see them. This asserts the mapping is total.
    """
    repository_row = {
        "total": 12,
        "due_today": 3,
        "overdue_count": 2,
        "mastered_count": 4,
        "learning_count": 5,
        "new_count": 3,
        "avg_ease_factor": 2.4,
        "recall_percent": 82,
        "reviewed_card_count": 9,
        "reviewed_total": 40,
        "reviewed_this_week": 11,
        "active_days_this_week": ["2026-08-10", "2026-08-11"],
        "current_streak": 2,
    }
    shaped = fs._shape_statistics(repository_row)
    assert shaped == {
        "total": 12,
        "dueToday": 3,
        # Due before the start of today, as distinct from `dueToday` (due by now). Published so the
        # Learn dashboard can read it here instead of issuing its own count query for the same table.
        "overdueCount": 2,
        "masteredCount": 4,
        "learningCount": 5,
        "newCount": 3,
        "averageEaseFactor": 2.4,
        "recallPercent": 82,
        "reviewedCardCount": 9,
        "reviewedTotal": 40,
        "reviewedThisWeek": 11,
        "activeDaysThisWeek": ["2026-08-10", "2026-08-11"],
        "currentStreak": 2,
    }
    assert shaped["masteredCount"] + shaped["learningCount"] + shaped["newCount"] == shaped["total"]


# ---------------------------------------------------------------------------
# Activity grouping
# ---------------------------------------------------------------------------


def _event(*, at: datetime, quality: int, deck: str | None, card: str | None, lapse=False):
    return {
        "reviewed_at": at,
        "quality": quality,
        "deck_id": deck,
        "flashcard_id": card,
        "was_lapse": lapse,
    }


class TestGroupActivity:
    def test_groups_one_entry_per_deck_per_day(self):
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        events = [
            _event(at=base, quality=4, deck="d1", card="c1"),
            _event(at=base + timedelta(minutes=5), quality=5, deck="d1", card="c2"),
            _event(at=base + timedelta(minutes=6), quality=3, deck="d2", card="c3"),
            _event(at=base - timedelta(days=1), quality=2, deck="d1", card="c1"),
        ]
        entries = fs.group_activity(
            events,
            deck_titles={"d1": "Algorithms", "d2": "Statistics"},
            learner_timezone=KNOWN_UTC,
            limit=10,
        )
        assert len(entries) == 3
        assert {(entry.deck_id, entry.card_count) for entry in entries} == {
            ("d1", 2),
            ("d2", 1),
            ("d1", 1),
        }
        assert entries[0].deck_title in {"Algorithms", "Statistics"}

    def test_counts_distinct_cards_not_grades(self):
        """Re-grading one card is one card reviewed, not two."""
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        events = [
            _event(at=base, quality=1, deck="d1", card="c1"),
            _event(at=base + timedelta(minutes=1), quality=4, deck="d1", card="c1"),
        ]
        entries = fs.group_activity(events, deck_titles={}, learner_timezone=KNOWN_UTC, limit=10)
        assert len(entries) == 1
        assert entries[0].card_count == 1
        # Recall still averages both grades, because both grades happened.
        assert entries[0].recall_percent == round((1 + 4) / 2 / 5 * 100)

    def test_falls_back_to_grade_count_when_the_card_is_gone(self):
        """A deleted card leaves a review row with no card id. It still counts."""
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        events = [
            _event(at=base, quality=4, deck="d1", card=None),
            _event(at=base + timedelta(minutes=1), quality=4, deck="d1", card=None),
        ]
        entries = fs.group_activity(events, deck_titles={}, learner_timezone=KNOWN_UTC, limit=10)
        assert entries[0].card_count == 2

    def test_newest_first_and_bounded(self):
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        events = [
            _event(at=base - timedelta(days=offset), quality=4, deck="d1", card=f"c{offset}")
            for offset in range(5)
        ]
        entries = fs.group_activity(events, deck_titles={}, learner_timezone=KNOWN_UTC, limit=3)
        assert len(entries) == 3
        assert entries == sorted(entries, key=lambda item: item.occurred_at, reverse=True)

    def test_days_are_the_learners_days(self):
        """Two reviews in the same UTC day can be two different local days.

        22:00 and 23:00 UTC on 12 August are 10:00 and 11:00 on 13 August in
        Auckland — the same local day. 09:00 UTC on 13 August is 21:00 on the 13th
        locally, still the same day, while 13:00 UTC on the 13th has crossed into the
        14th. Grouping on UTC dates would split the first pair from the third and
        merge the fourth, which is how streaks and weekly counts drift.
        """
        events = [
            _event(at=datetime(2026, 8, 12, 22, 0, tzinfo=UTC), quality=4, deck="d", card="c1"),
            _event(at=datetime(2026, 8, 12, 23, 0, tzinfo=UTC), quality=4, deck="d", card="c2"),
            _event(at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC), quality=4, deck="d", card="c3"),
            _event(at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC), quality=4, deck="d", card="c4"),
        ]
        local = fs.group_activity(events, deck_titles={}, learner_timezone=AUCKLAND, limit=10)
        assert sorted(entry.card_count for entry in local) == [1, 3]

        utc = fs.group_activity(events, deck_titles={}, learner_timezone=KNOWN_UTC, limit=10)
        assert sorted(entry.card_count for entry in utc) == [2, 2]

    def test_reports_graduations_and_creations_alongside_reviews(self):
        """The feed shows three kinds of progress, not only review.

        A learner who spent the week writing cards did something, and a review-only
        feed would be blank for them.
        """
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        entries = fs.group_activity(
            [_event(at=base, quality=4, deck="d1", card="c1")],
            deck_titles={"d1": "Algorithms"},
            graduations=[{"occurred_at": base, "deck_id": "d1", "flashcard_id": "c1"}],
            creations=[
                {"occurred_at": base, "deck_id": "d1", "flashcard_id": "c9"},
                {"occurred_at": base, "deck_id": "d1", "flashcard_id": "c8"},
            ],
            learner_timezone=KNOWN_UTC,
            limit=10,
        )
        by_kind = {entry.kind: entry for entry in entries}
        assert set(by_kind) == {"reviewed", "graduated", "created"}
        assert by_kind["created"].card_count == 2
        assert by_kind["graduated"].card_count == 1
        assert by_kind["reviewed"].recall_percent == 80
        # Writing or graduating a card produces no recall figure, so it is absent
        # rather than zero.
        assert by_kind["created"].recall_percent is None
        assert by_kind["graduated"].recall_percent is None
        assert all(entry.deck_title == "Algorithms" for entry in entries)

    def test_entry_ids_are_unique_across_kinds_on_the_same_day(self):
        """The client keys a list on these; a collision would drop rows."""
        base = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
        entries = fs.group_activity(
            [_event(at=base, quality=4, deck="d1", card="c1")],
            deck_titles={},
            graduations=[{"occurred_at": base, "deck_id": "d1", "flashcard_id": "c1"}],
            creations=[{"occurred_at": base, "deck_id": "d1", "flashcard_id": "c1"}],
            learner_timezone=KNOWN_UTC,
            limit=10,
        )
        assert len({entry.id for entry in entries}) == len(entries) == 3

    def test_a_week_of_only_writing_still_produces_a_feed(self):
        entries = fs.group_activity(
            [],
            deck_titles={"d1": "German"},
            creations=[
                {
                    "occurred_at": datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
                    "deck_id": "d1",
                    "flashcard_id": "c1",
                }
            ],
            learner_timezone=KNOWN_UTC,
            limit=10,
        )
        assert [entry.kind for entry in entries] == ["created"]

    def test_ignores_rows_with_no_timestamp(self):
        entries = fs.group_activity(
            [_event(at=None, quality=4, deck="d", card="c")],
            deck_titles={},
            learner_timezone=KNOWN_UTC,
            limit=5,
        )
        assert entries == []


# ---------------------------------------------------------------------------
# Insight ladder
# ---------------------------------------------------------------------------


def _reviews(count: int, *, hour: int, quality: int) -> list[dict]:
    base = datetime(2026, 8, 10, hour, 0, tzinfo=UTC)
    return [
        _event(at=base + timedelta(minutes=index), quality=quality, deck="d", card=f"c{index}")
        for index in range(count)
    ]


class TestChooseInsight:
    def test_empty_library_says_so(self):
        insight = fs.choose_insight(
            events=[], due_today=0, overdue=0, total_cards=0, lapsing_cards=0
        )
        assert insight.kind == "empty_library"

    def test_overdue_outranks_due(self):
        insight = fs.choose_insight(
            events=[], due_today=9, overdue=4, total_cards=30, lapsing_cards=2
        )
        assert insight.kind == "overdue_backlog"
        assert "4 cards" in insight.title

    def test_due_outranks_lapsing(self):
        insight = fs.choose_insight(
            events=[], due_today=9, overdue=0, total_cards=30, lapsing_cards=2
        )
        assert insight.kind == "due_now"
        # The estimate uses the one shared per-card constant.
        assert f"{(9 * fs.REVIEW_SECONDS_PER_CARD + 59) // 60} minutes" in insight.body

    def test_singular_wording_for_one_card(self):
        insight = fs.choose_insight(
            events=[], due_today=1, overdue=0, total_cards=5, lapsing_cards=0
        )
        assert "1 card are" not in insight.title
        assert insight.title == "1 card is ready now"

    def test_lapsing_cards_when_nothing_is_due(self):
        insight = fs.choose_insight(
            events=[], due_today=0, overdue=0, total_cards=30, lapsing_cards=3
        )
        assert insight.kind == "lapsing_cards"

    def test_settled_library_gets_a_plain_summary(self):
        insight = fs.choose_insight(
            events=[], due_today=0, overdue=0, total_cards=30, lapsing_cards=0
        )
        assert insight.kind == "library_summary"
        assert "30 cards" in insight.body

    def test_time_of_day_rung_reports_real_numbers(self):
        events = _reviews(12, hour=8, quality=5) + _reviews(12, hour=20, quality=2)
        insight = fs.choose_insight(
            events=events,
            learner_timezone=KNOWN_UTC,
            due_today=5,
            overdue=0,
            total_cards=40,
            lapsing_cards=0,
        )
        assert insight.kind == "best_time_of_day"
        assert "morning" in insight.title
        # 100% in the morning against 70% overall.
        assert "100%" in insight.body
        assert "70%" in insight.body

    def test_time_of_day_rung_is_withheld_when_the_zone_is_unknown(self):
        """ "You recall best in the morning" is a claim about the learner's morning."""
        events = _reviews(12, hour=8, quality=5) + _reviews(12, hour=20, quality=2)
        insight = fs.choose_insight(
            events=events,
            learner_timezone=UNKNOWN_TIMEZONE,
            due_today=5,
            overdue=0,
            total_cards=40,
            lapsing_cards=0,
        )
        assert insight.kind == "due_now"

    def test_time_of_day_rung_needs_enough_reviews(self):
        events = _reviews(6, hour=8, quality=5) + _reviews(6, hour=20, quality=2)
        insight = fs.choose_insight(
            events=events,
            learner_timezone=KNOWN_UTC,
            due_today=5,
            overdue=0,
            total_cards=40,
            lapsing_cards=0,
        )
        assert insight.kind == "due_now"

    def test_time_of_day_rung_needs_a_real_difference(self):
        """A flat learner is told nothing about time of day."""
        events = _reviews(12, hour=8, quality=4) + _reviews(12, hour=20, quality=4)
        insight = fs.choose_insight(
            events=events,
            learner_timezone=KNOWN_UTC,
            due_today=5,
            overdue=0,
            total_cards=40,
            lapsing_cards=0,
        )
        assert insight.kind == "due_now"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"due_today": 0, "overdue": 0, "total_cards": 0, "lapsing_cards": 0},
            {"due_today": 0, "overdue": 0, "total_cards": 9, "lapsing_cards": 0},
            {"due_today": 3, "overdue": 1, "total_cards": 9, "lapsing_cards": 5},
        ],
    )
    def test_always_returns_something_renderable(self, kwargs):
        """The page renders this unconditionally, so no input may produce nothing."""
        insight = fs.choose_insight(events=[], **kwargs)
        assert insight.title and insight.body and insight.action_label
