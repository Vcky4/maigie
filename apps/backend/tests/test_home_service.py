"""Unit tests for Home Service pure helper functions (no DB required)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.domains.personal_learning.services.home_service import (
    _build_due_reviews,
    _build_greeting,
    _build_progress_summary,
    _build_recommendations,
    _check_re_engagement,
    _compute_next_action,
)

# ---------------------------------------------------------------------------
# Fake dataclasses to mimic domain models
# ---------------------------------------------------------------------------


@dataclass
class FakeProfile:
    maturity_days: int = 0
    consistency_score: float | None = None
    avg_session_minutes: float | None = None
    updated_at: datetime | None = None


@dataclass
class FakeFlashcard:
    id: str = "card1"
    front: str = "What is X?"
    next_review_at: datetime | None = None
    deck_id: str | None = None
    deck: object | None = None
    repetition_count: int = 0
    interval_days: int = 1
    last_reviewed_at: datetime | None = None


@dataclass
class FakeRecommendation:
    id: str = "rec1"
    item_type: str = "topic"
    title: str = "Learn Python"
    reason: str = "Matches your goals"


# ---------------------------------------------------------------------------
# TestBuildGreeting
# ---------------------------------------------------------------------------


class TestBuildGreeting:
    """Tests for _build_greeting — time-of-day and context-based greetings."""

    def test_morning_greeting(self):
        """Hour 8 → starts with 'Good morning'."""
        fake_now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(), None)
        assert result.startswith("Good morning")

    def test_afternoon_greeting(self):
        """Hour 14 → starts with 'Good afternoon'."""
        fake_now = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(), None)
        assert result.startswith("Good afternoon")

    def test_evening_greeting(self):
        """Hour 19 → starts with 'Good evening'."""
        fake_now = datetime(2024, 6, 15, 19, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(), None)
        assert result.startswith("Good evening")

    def test_night_greeting(self):
        """Hour 23 → starts with 'Hello'."""
        fake_now = datetime(2024, 6, 15, 23, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(), None)
        assert result.startswith("Hello")

    def test_streak_milestone_mention(self):
        """maturity_days=14 (multiple of 7) → mentions 14 days."""
        fake_now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(maturity_days=14), None)
        assert "14 days" in result

    def test_consistency_mention(self):
        """consistency >= 80 → mentions consistency."""
        fake_now = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        with patch("src.domains.personal_learning.services.home_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = _build_greeting(FakeProfile(consistency_score=85.0), None)
        assert "consistency" in result.lower()


# ---------------------------------------------------------------------------
# TestBuildProgressSummary
# ---------------------------------------------------------------------------


class TestBuildProgressSummary:
    """Tests for _build_progress_summary."""

    def test_returns_expected_keys(self):
        profile = FakeProfile(maturity_days=10, avg_session_minutes=20.0)
        stats = {"masteredCount": 5, "currentStreak": 3}
        result = _build_progress_summary(profile, stats)
        assert "currentStreak" in result
        assert "weeklyMinutes" in result
        assert "topicsCompletedThisWeek" in result

    def test_streak_from_flashcard_stats(self):
        profile = FakeProfile(maturity_days=7)
        result = _build_progress_summary(profile, {"currentStreak": 4})
        assert result["currentStreak"] == 4

    def test_the_week_figures_are_reported_not_invented(self):
        """Both were hardcoded — `weeklyMinutes` to `None` and `topicsCompletedThisWeek` to `0`, for
        every learner on every request. They now come from `_week_so_far`, and the builder's job is to
        report what it was given rather than to decide it."""
        profile = FakeProfile(avg_session_minutes=30.0)
        result = _build_progress_summary(profile, {}, weekly_minutes=42.5, topics_completed=3)
        assert result["weeklyMinutes"] == 42.5
        assert result["topicsCompletedThisWeek"] == 3

    def test_unmeasured_minutes_stay_none_rather_than_zero(self):
        """Almost nothing writes `StudySession`, so `None` is the common answer. `0` would tell the
        learner they studied nothing, which is a different claim from nothing being measured."""
        result = _build_progress_summary(FakeProfile(), {}, weekly_minutes=None)
        assert result["weeklyMinutes"] is None

    def test_none_profile_uses_defaults(self):
        result = _build_progress_summary(None, {"masteredCount": 3})
        assert result["currentStreak"] == 0
        assert result["weeklyMinutes"] is None
        assert result["topicsCompletedThisWeek"] == 0


# ---------------------------------------------------------------------------
# TestBuildDueReviews
# ---------------------------------------------------------------------------


class TestBuildDueReviews:
    """Tests for _build_due_reviews — building review items from flashcards."""

    def test_empty_flashcards(self):
        """Empty list → empty result."""
        result = _build_due_reviews([])
        assert result == []

    def test_caps_at_10(self):
        """15 due cards → only 10 in output."""
        cards = [FakeFlashcard(id=f"card{i}", front=f"Q{i}") for i in range(15)]
        result = _build_due_reviews(cards)
        assert len(result) == 10

    def test_urgency_increases_with_overdue(self):
        """More overdue = higher urgency value."""
        now = datetime.now(UTC)
        # Card 1: due 1 hour ago (urgency 1)
        card_recent = FakeFlashcard(
            id="recent", front="Recent", next_review_at=now - timedelta(hours=1)
        )
        # Card 2: due 5 days ago (urgency should be higher)
        card_old = FakeFlashcard(id="old", front="Old", next_review_at=now - timedelta(days=5))
        result = _build_due_reviews([card_recent, card_old])
        assert result[1]["urgency"] > result[0]["urgency"]

    def test_review_item_structure(self):
        """Each item has id, type, title, dueAt, urgency."""
        now = datetime.now(UTC)
        card = FakeFlashcard(id="c1", front="Hello world", next_review_at=now)
        result = _build_due_reviews([card])
        assert len(result) == 1
        item = result[0]
        assert item["id"] == "c1"
        assert item["type"] == "flashcard"
        assert item["title"] == "Hello world"
        assert item["dueAt"] is not None
        assert "urgency" in item

    def test_none_next_review_at(self):
        """Card with no next_review_at → dueAt is None, urgency defaults to 1."""
        card = FakeFlashcard(id="c1", front="No date", next_review_at=None)
        result = _build_due_reviews([card])
        assert result[0]["dueAt"] is None
        assert result[0]["urgency"] == 1


# ---------------------------------------------------------------------------
# TestComputeNextAction
# ---------------------------------------------------------------------------


class TestComputeNextAction:
    """Tests for _compute_next_action — priority-based next action."""

    def test_due_flashcards_highest_priority(self):
        """When cards are due, next action is review."""
        cards = [FakeFlashcard(id=f"c{i}") for i in range(5)]
        result = _compute_next_action(cards, {"topicTitle": "Foo"}, [])
        assert result["type"] == "review_flashcards"
        assert "5" in result["title"]

    def test_schedule_block_next_if_no_cards(self):
        """No cards but blocks → scheduled_study."""
        blocks = [{"id": "b1", "title": "Study Chapter 3"}]
        result = _compute_next_action([], {"topicTitle": "Foo"}, blocks)
        assert result["type"] == "scheduled_study"
        assert result["title"] == "Study Chapter 3"
        assert result["actionData"]["blockId"] == "b1"

    def test_focus_if_no_cards_no_blocks(self):
        """No cards, no blocks, but focus → continue_study."""
        focus = {"topicTitle": "Continue algorithms"}
        result = _compute_next_action([], focus, [])
        assert result["type"] == "continue_study"
        assert result["title"] == "Continue algorithms"

    def test_explore_when_nothing_available(self):
        """No cards, no blocks, no focus → explore."""
        result = _compute_next_action([], None, [])
        assert result["type"] == "explore"
        assert result["title"] == "Explore something new"

    def test_review_caps_at_10_in_title(self):
        """Even with 20 cards, title says 'Review 10 flashcards'."""
        cards = [FakeFlashcard(id=f"c{i}") for i in range(20)]
        result = _compute_next_action(cards, None, [])
        assert "10" in result["title"]


# ---------------------------------------------------------------------------
# TestCheckReengagement
# ---------------------------------------------------------------------------


class TestCheckReengagement:
    """Tests for _check_re_engagement — returning gentle nudge when away."""

    def test_no_profile_returns_none(self):
        """None profile → None."""
        result = _check_re_engagement(None)
        assert result is None

    def test_recent_activity_returns_none(self):
        """updated_at = yesterday → None (not away long enough)."""
        profile = FakeProfile(updated_at=datetime.now(UTC) - timedelta(days=1))
        result = _check_re_engagement(profile)
        assert result is None

    def test_away_8_days_returns_message(self):
        """updated_at = 8 days ago → returns re-engagement dict."""
        profile = FakeProfile(updated_at=datetime.now(UTC) - timedelta(days=8))
        result = _check_re_engagement(profile)
        assert result is not None
        assert "message" in result
        assert result["daysAway"] == 8
        assert "suggestedAction" in result

    def test_away_exactly_7_days_returns_none(self):
        """Boundary: exactly 7 days → None (must be > 7)."""
        profile = FakeProfile(updated_at=datetime.now(UTC) - timedelta(days=7))
        result = _check_re_engagement(profile)
        assert result is None

    def test_no_updated_at_returns_none(self):
        """Profile with updated_at=None → None."""
        profile = FakeProfile(updated_at=None)
        result = _check_re_engagement(profile)
        assert result is None


# ---------------------------------------------------------------------------
# TestBuildRecommendations
# ---------------------------------------------------------------------------


class TestBuildRecommendations:
    """Tests for _build_recommendations — onboarding vs mature paths."""

    def test_onboarding_returns_discovery_actions(self):
        """is_onboarding=True → onboarding items."""
        result = _build_recommendations([], is_onboarding=True)
        assert len(result) >= 1
        assert all(r["type"] == "onboarding" for r in result)

    def test_mature_returns_recommendations(self):
        """is_onboarding=False with recs → formatted list."""
        recs = [
            FakeRecommendation(id="r1", title="Python Basics", reason="Popular"),
            FakeRecommendation(id="r2", title="Data Structures", reason="Foundational"),
        ]
        result = _build_recommendations(recs, is_onboarding=False)
        assert len(result) == 2
        assert result[0]["title"] == "Python Basics"
        assert result[1]["title"] == "Data Structures"
        assert result[0]["actionData"]["recommendationId"] == "r1"

    def test_empty_recommendations_returns_empty_list(self):
        """No recs → []."""
        result = _build_recommendations([], is_onboarding=False)
        assert result == []

    def test_caps_at_5(self):
        """10 recs → max 5 in output."""
        recs = [
            FakeRecommendation(id=f"r{i}", title=f"Topic {i}", reason=f"Reason {i}")
            for i in range(10)
        ]
        result = _build_recommendations(recs, is_onboarding=False)
        assert len(result) == 5

    def test_recommendation_structure(self):
        """Each recommendation has type, title, reason, actionData."""
        recs = [FakeRecommendation(id="r1", title="ML Intro", reason="Trending")]
        result = _build_recommendations(recs, is_onboarding=False)
        item = result[0]
        assert item["type"] == "topic"
        assert item["title"] == "ML Intro"
        assert item["reason"] == "Trending"
        assert item["actionData"]["recommendationId"] == "r1"


class TestWeekSoFar:
    """The two figures that used to be placeholders.

    `weeklyMinutes` and `topicsCompletedThisWeek` were hardcoded to `None` and `0` on every home
    response, with a comment saying they would stay that way until a real source existed. Both sources
    did exist: `knowledge_repo.completed_topic_dates` and `StudySession.duration`.
    """

    async def _week(self, *, completions=(), week_sessions=None, any_sessions=None):
        """Run `_week_so_far` with both repository reads faked."""
        from datetime import UTC, datetime, timedelta
        from unittest.mock import patch

        from src.domains.personal_learning.services import home_service

        now = datetime.now(UTC)
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        class _Knowledge:
            async def completed_topic_dates(self, user_id, *, since=None):
                return [monday + offset for offset in completions]

        class _Progress:
            async def list_sessions(self, user_id, *, since=None, course_id=None):
                if since is None:
                    return list(any_sessions or [])
                return list(week_sessions or [])

        from src.shared.time import UNKNOWN_TIMEZONE

        async def _timezone(_user_id):
            return UNKNOWN_TIMEZONE

        with (
            patch("src.shared.time.resolve_learner_timezone", _timezone),
            patch("src.domains.knowledge.repository.knowledge_repo", _Knowledge()),
            patch("src.domains.progress.repository.progress_repo", _Progress()),
        ):
            return await home_service._week_so_far("u1")

    def _session(self, *, days_into_week: float, minutes: float):
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace

        now = datetime.now(UTC)
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return SimpleNamespace(start_time=monday + timedelta(days=days_into_week), duration=minutes)

    async def test_topics_completed_this_week_are_counted(self):
        from datetime import timedelta

        minutes, topics = await self._week(
            completions=(timedelta(hours=2), timedelta(days=1, hours=3))
        )
        assert topics == 2
        assert minutes is None, "no sessions anywhere means nothing was measured"

    async def test_a_completion_outside_the_week_is_not_counted(self):
        from datetime import timedelta

        _, topics = await self._week(completions=(timedelta(days=9),))
        assert topics == 0, "a completion beyond this week belongs to the next one"

    async def test_minutes_are_summed_from_the_week(self):
        minutes, _ = await self._week(
            week_sessions=[
                self._session(days_into_week=0, minutes=20.0),
                self._session(days_into_week=2, minutes=22.5),
            ]
        )
        assert minutes == 42.5

    async def test_a_learner_who_tracks_time_but_not_this_week_gets_zero(self):
        """A real finding — "you recorded nothing this week" — and different from unmeasured."""
        minutes, _ = await self._week(
            week_sessions=[], any_sessions=[self._session(days_into_week=-30, minutes=15.0)]
        )
        assert minutes == 0.0

    async def test_a_learner_who_has_never_tracked_time_gets_none(self):
        minutes, _ = await self._week(week_sessions=[], any_sessions=[])
        assert minutes is None
