"""Unit tests for study plan distribution pure logic (no DB required)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import datetime, timedelta, timezone

import pytest

from src.domains.personal_learning.services.study_plan_service import (
    _add_review_items,
    _distribute_items,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)


def _make_topics(n: int, minutes: int = 30) -> list[dict]:
    """Create n dummy topics with a given estimatedMinutes."""
    return [
        {
            "title": f"Topic {i+1}",
            "estimatedMinutes": minutes,
            "type": "STUDY",
            "topicId": None,
            "prepTopicId": None,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# TestDistributeItems
# ---------------------------------------------------------------------------


class TestDistributeItems:
    """Tests for _distribute_items pure scheduling logic."""

    def test_single_topic_single_day(self):
        """1 topic, 1 day available -> 1 item on day 1."""
        topics = _make_topics(1)
        result = _distribute_items(topics, days_available=1, start=START, max_daily_minutes=120)

        assert len(result) == 1
        assert result[0]["scheduledDate"] == START + timedelta(days=1)

    def test_topics_within_daily_limit(self):
        """3 topics at 30min each, limit 120min -> all fit on day 1."""
        topics = _make_topics(3, minutes=30)
        result = _distribute_items(topics, days_available=7, start=START, max_daily_minutes=120)

        # Total is 90min < 120min, so all items should be on the same day
        dates = {item["scheduledDate"] for item in result}
        assert len(dates) == 1
        assert len(result) == 3

    def test_topics_exceed_daily_limit(self):
        """5 topics at 30min, limit 60min -> spreads across multiple days."""
        topics = _make_topics(5, minutes=30)
        result = _distribute_items(topics, days_available=7, start=START, max_daily_minutes=60)

        # 60min limit with 30min topics -> 2 per day, so 5 topics need 3 days
        dates = {item["scheduledDate"] for item in result}
        assert len(dates) >= 2
        assert len(result) == 5

    def test_wraps_around_when_exceeding_days(self):
        """More topics than days can hold -> wraps around to earlier days."""
        topics = _make_topics(10, minutes=60)
        # 2 days available, 60min limit -> 1 topic per day, wraps around
        result = _distribute_items(topics, days_available=2, start=START, max_daily_minutes=60)

        assert len(result) == 10
        # All scheduled dates should be within the 2-day window
        expected_dates = {START + timedelta(days=1), START + timedelta(days=2)}
        actual_dates = {item["scheduledDate"] for item in result}
        assert actual_dates == expected_dates

    def test_respects_max_daily_minutes(self):
        """No single day exceeds the daily limit."""
        topics = _make_topics(8, minutes=25)
        max_daily = 60.0
        result = _distribute_items(topics, days_available=10, start=START, max_daily_minutes=max_daily)

        # Group by date and sum minutes
        daily_totals: dict[datetime, float] = {}
        for item in result:
            date = item["scheduledDate"]
            daily_totals[date] = daily_totals.get(date, 0) + item["estimatedMinutes"]

        for date, total in daily_totals.items():
            assert total <= max_daily, f"Day {date} has {total}min exceeding limit {max_daily}"

    def test_all_topics_appear_in_output(self):
        """Every input topic has a corresponding output item."""
        topics = _make_topics(6, minutes=20)
        result = _distribute_items(topics, days_available=5, start=START, max_daily_minutes=90)

        output_titles = {item["title"] for item in result}
        input_titles = {t["title"] for t in topics}
        assert output_titles == input_titles

    def test_scheduled_dates_within_deadline(self):
        """All dates are between start and start + days_available (inclusive)."""
        topics = _make_topics(4, minutes=30)
        days = 7
        result = _distribute_items(topics, days_available=days, start=START, max_daily_minutes=120)

        earliest_allowed = START + timedelta(days=1)
        latest_allowed = START + timedelta(days=days)
        for item in result:
            assert earliest_allowed <= item["scheduledDate"] <= latest_allowed

    def test_empty_topics_returns_empty(self):
        """Empty input -> empty output."""
        result = _distribute_items([], days_available=5, start=START, max_daily_minutes=120)
        assert result == []


# ---------------------------------------------------------------------------
# TestAddReviewItems
# ---------------------------------------------------------------------------


class TestAddReviewItems:
    """Tests for _add_review_items spaced repetition scheduling."""

    def test_adds_reviews_for_first_third(self):
        """Only the first third of items get review items."""
        topics = _make_topics(9, minutes=30)
        plan_items = _distribute_items(topics, days_available=30, start=START, max_daily_minutes=120)
        reviews = _add_review_items(plan_items, days_available=30, start=START)

        # 9 items // 3 = 3 items get reviews
        assert len(reviews) == 3

    def test_review_scheduled_3_days_after_study(self):
        """Review date = study date + 3 days."""
        topics = _make_topics(3, minutes=30)
        plan_items = _distribute_items(topics, days_available=30, start=START, max_daily_minutes=120)
        reviews = _add_review_items(plan_items, days_available=30, start=START)

        # First item gets a review
        assert len(reviews) >= 1
        study_date = plan_items[0]["scheduledDate"]
        review_date = reviews[0]["scheduledDate"]
        assert review_date == study_date + timedelta(days=3)

    def test_no_reviews_past_deadline(self):
        """Reviews scheduled past plan end date are excluded."""
        topics = _make_topics(6, minutes=30)
        # Use short deadline so reviews at +3 days would fall outside
        days = 2
        plan_items = _distribute_items(topics, days_available=days, start=START, max_daily_minutes=120)
        reviews = _add_review_items(plan_items, days_available=days, start=START)

        plan_end = START + timedelta(days=days)
        for review in reviews:
            assert review["scheduledDate"] <= plan_end

    def test_review_type_is_REVIEW(self):
        """All review items have type = 'REVIEW'."""
        topics = _make_topics(6, minutes=30)
        plan_items = _distribute_items(topics, days_available=30, start=START, max_daily_minutes=120)
        reviews = _add_review_items(plan_items, days_available=30, start=START)

        for review in reviews:
            assert review["type"] == "REVIEW"

    def test_review_estimated_minutes_is_15(self):
        """Review items are always 15 minutes."""
        topics = _make_topics(6, minutes=45)
        plan_items = _distribute_items(topics, days_available=30, start=START, max_daily_minutes=120)
        reviews = _add_review_items(plan_items, days_available=30, start=START)

        for review in reviews:
            assert review["estimatedMinutes"] == 15

    def test_empty_plan_no_reviews(self):
        """Empty plan -> no reviews."""
        reviews = _add_review_items([], days_available=10, start=START)
        assert reviews == []


# ---------------------------------------------------------------------------
# TestStudyPlanCoverageInvariant
# ---------------------------------------------------------------------------


class TestStudyPlanCoverageInvariant:
    """Integration-level invariants across distribute + review functions."""

    def test_all_topics_covered(self):
        """Given N topics, all N appear in the distributed items."""
        n = 12
        topics = _make_topics(n, minutes=25)
        plan_items = _distribute_items(topics, days_available=7, start=START, max_daily_minutes=90)

        output_titles = {item["title"] for item in plan_items}
        input_titles = {t["title"] for t in topics}
        assert output_titles == input_titles
        assert len(plan_items) == n

    def test_no_day_exceeds_sustainable_limit(self):
        """Group items by date, sum estimated_minutes per day, verify none exceeds max."""
        topics = _make_topics(15, minutes=20)
        max_daily = 80.0
        plan_items = _distribute_items(
            topics, days_available=10, start=START, max_daily_minutes=max_daily
        )

        daily_totals: dict[datetime, float] = {}
        for item in plan_items:
            date = item["scheduledDate"]
            daily_totals[date] = daily_totals.get(date, 0) + item["estimatedMinutes"]

        for date, total in daily_totals.items():
            assert total <= max_daily, (
                f"Day {date.date()} has {total}min, exceeding sustainable limit {max_daily}min"
            )
