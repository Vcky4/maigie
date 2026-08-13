"""Flashcard repository queries, exercised against a real database engine.

SQLite in memory rather than Postgres, so these run everywhere instead of skipping
whenever ``DATABASE_URL`` is unset. That trade is worth making for this particular set:
the queries added in this stage are grouped aggregates, a conditional-count forecast, a
correlated replay subquery and a two-statement transaction, and none of those is checked
by asserting on the Python around them.

What SQLite cannot cover is noted per test. The zone-aware day grouping uses
``timezone(name, timestamptz)``, which is Postgres-only, so these run in the UTC
fallback path; the zone-aware behaviour is covered by the pure grouping tests in
``test_flashcard_dashboard.py`` and by the API tests against Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "repo-test-user"
OTHER_USER = "repo-test-intruder"


@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle():
    """Shadow the Postgres lifecycle fixture from conftest.

    Declared here rather than by setting ``SKIP_DB_FIXTURE`` in the environment,
    because that variable is process-wide: a module that sets it at import time
    silently disables the database fixture for every other module collected in the
    same run, including ones that need it.
    """
    yield


@pytest.fixture
async def repo(monkeypatch):
    """A repository bound to a fresh in-memory database, with one learner in it."""
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.personal_learning import db_models as pl_models
    from src.domains.personal_learning import repository as repository_module
    from src.shared.database.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # SQLite ignores foreign keys unless asked not to, and without this the
    # ``ON DELETE SET NULL`` behaviour these tests assert would silently not happen —
    # the rows would survive with stale ids and the tests would pass for the wrong
    # reason. With the pragma on, a detach is a real detach and a bad reference is a
    # real error.
    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Only the tables these queries touch, plus the parents their foreign keys point
    # at. Creating the whole shared metadata is not an option: it is one namespace for
    # every domain, and some of it uses Postgres-only column types that SQLite cannot
    # emit. Which tables those are depends on what else the run imported, so a
    # whole-metadata create would pass or fail depending on the rest of the suite.
    tables = [
        identity_models.User.__table__,
        pl_models.ExamPrep.__table__,
        pl_models.FlashcardDeck.__table__,
        pl_models.Flashcard.__table__,
        pl_models.FlashcardReview.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        session.add(identity_models.User(id=OTHER_USER, email="intruder@example.com"))
        await session.commit()

    yield repository_module.personal_learning_repo
    await engine.dispose()


async def _card(repo, *, deck_id, front, interval, reviewed_days_ago, quality, next_review_in):
    now = datetime.now(UTC)
    return await repo.create_flashcard(
        {
            "userId": USER,
            "deckId": deck_id,
            "front": front,
            "back": f"answer to {front}",
            "intervalDays": interval,
            "repetitionCount": 5 if interval >= 21 else 0,
            "easeFactor": 2.5,
            "nextReviewAt": now + next_review_in,
            "lastReviewedAt": (
                now - timedelta(days=reviewed_days_ago) if reviewed_days_ago is not None else None
            ),
            "lastQuality": quality,
            "lapseCount": 0,
        }
    )


@pytest.fixture
async def library(repo):
    """A deck with four cards spanning every scheduling state."""
    deck = await repo.create_deck({"userId": USER, "title": "Algorithms", "subject": "CS"})
    cards = [
        # Two mature cards, reviewed yesterday, next due in three days.
        await _card(
            repo,
            deck_id=deck.id,
            front="mature-1",
            interval=30,
            reviewed_days_ago=1,
            quality=4,
            next_review_in=timedelta(days=3),
        ),
        await _card(
            repo,
            deck_id=deck.id,
            front="mature-2",
            interval=30,
            reviewed_days_ago=1,
            quality=4,
            next_review_in=timedelta(days=3),
        ),
        # One card in repetition but not yet mature.
        await _card(
            repo,
            deck_id=deck.id,
            front="learning-1",
            interval=6,
            reviewed_days_ago=1,
            quality=4,
            next_review_in=timedelta(days=3),
        ),
        # One never reviewed, and already overdue.
        await _card(
            repo,
            deck_id=deck.id,
            front="new-1",
            interval=1,
            reviewed_days_ago=None,
            quality=-1,
            next_review_in=-timedelta(hours=1),
        ),
    ]
    return deck, cards


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestStatistics:
    async def test_states_partition_the_library(self, repo, library):
        stats = await repo.get_flashcard_stats(USER)
        assert stats["total"] == 4
        assert stats["mastered_count"] == 2
        assert stats["learning_count"] == 1
        assert stats["new_count"] == 1
        assert (
            stats["mastered_count"] + stats["learning_count"] + stats["new_count"] == stats["total"]
        )

    async def test_recall_excludes_the_never_reviewed_sentinel(self, repo, library):
        """``lastQuality`` is -1 until first review, and 0 is a real grade.

        Filtering on the timestamp rather than on the value is what keeps a card
        graded 0 in the average and the unreviewed sentinel out of it.
        """
        stats = await repo.get_flashcard_stats(USER)
        # Three reviewed cards, all graded 4: 4/5 = 80%.
        assert stats["reviewed_card_count"] == 3
        assert stats["recall_percent"] == 80

    async def test_recall_is_unknown_rather_than_zero_for_an_untouched_library(self, repo):
        await repo.create_deck({"userId": USER, "title": "Empty"})
        stats = await repo.get_flashcard_stats(USER)
        assert stats["recall_percent"] is None

    async def test_history_counts_start_empty_and_are_not_inferred_from_cards(self, repo, library):
        """Three cards carry a ``lastReviewedAt`` and the log is empty.

        The old derivation would have reported three reviews and a streak from those
        timestamps. Migration 020 deliberately does not back-fill, so the honest
        answer is that no reviews have been recorded.
        """
        stats = await repo.get_flashcard_stats(USER)
        assert stats["reviewed_total"] == 0
        assert stats["current_streak"] == 0
        assert stats["active_days_this_week"] == []

    async def test_deck_scope_counts_only_that_deck(self, repo, library):
        deck, _ = library
        other = await repo.create_deck({"userId": USER, "title": "Statistics"})
        await _card(
            repo,
            deck_id=other.id,
            front="elsewhere",
            interval=1,
            reviewed_days_ago=None,
            quality=-1,
            next_review_in=timedelta(days=1),
        )
        assert (await repo.get_flashcard_stats(USER, deck_id=deck.id))["total"] == 4
        assert (await repo.get_flashcard_stats(USER, deck_id=other.id))["total"] == 1
        assert (await repo.get_flashcard_stats(USER))["total"] == 5

    async def test_another_learner_sees_none_of_it(self, repo, library):
        assert (await repo.get_flashcard_stats(OTHER_USER))["total"] == 0


# ---------------------------------------------------------------------------
# Deck aggregates
# ---------------------------------------------------------------------------


class TestDeckAggregates:
    async def test_one_query_returns_every_figure_the_deck_card_shows(self, repo, library):
        deck, _ = library
        rows = await repo.list_decks_with_stats(USER)
        assert len(rows) == 1
        row = rows[0]
        assert row["deck"].id == deck.id
        assert row["card_count"] == 4
        assert row["due_count"] == 1
        assert row["mastered_count"] == 2
        assert row["reviewed_count"] == 3
        assert row["recall_percent"] == 80
        assert row["last_reviewed_at"] is not None
        assert row["next_review_at"] is not None

    async def test_an_empty_deck_still_appears(self, repo):
        """A deck with no cards is a deck the learner created and must see."""
        await repo.create_deck({"userId": USER, "title": "Empty"})
        rows = await repo.list_decks_with_stats(USER)
        assert len(rows) == 1
        assert rows[0]["card_count"] == 0
        assert rows[0]["recall_percent"] is None

    async def test_scoped_to_one_deck(self, repo, library):
        deck, _ = library
        await repo.create_deck({"userId": USER, "title": "Other"})
        rows = await repo.list_decks_with_stats(USER, deck_id=deck.id)
        assert [row["deck"].id for row in rows] == [deck.id]

    async def test_another_learners_deck_is_not_returned(self, repo, library):
        deck, _ = library
        assert await repo.list_decks_with_stats(OTHER_USER, deck_id=deck.id) == []

    async def test_learner_columns_round_trip(self, repo):
        deck = await repo.create_deck(
            {
                "userId": USER,
                "title": "German",
                "subject": "Language",
                "accent": "orange",
                "dailyGoal": 15,
            }
        )
        assert deck.subject == "Language"
        assert deck.accent == "orange"
        assert deck.daily_goal == 15


# ---------------------------------------------------------------------------
# Reviewing
# ---------------------------------------------------------------------------


def _review_payload(deck_id, *, quality, interval, lapse=False, at=None):
    return {
        "deckId": deck_id,
        "quality": quality,
        "intervalDays": interval,
        "easeFactor": 2.6,
        "repetitionCount": 6,
        "wasLapse": lapse,
        "reviewedAt": at or datetime.now(UTC),
    }


def _card_update(*, interval, at=None):
    now = at or datetime.now(UTC)
    return {
        "intervalDays": interval,
        "repetitionCount": 6,
        "easeFactor": 2.6,
        "nextReviewAt": now + timedelta(days=interval),
        "lastReviewedAt": now,
        "lastQuality": 5,
        "lapseCount": 0,
    }


class TestApplyReview:
    async def test_advances_the_card_and_logs_the_grade(self, repo, library):
        deck, cards = library
        updated = await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=60),
            review=_review_payload(deck.id, quality=5, interval=60),
        )
        assert updated.interval_days == 60

        events = await repo.list_review_events(USER, since=datetime.now(UTC) - timedelta(days=1))
        assert len(events) == 1
        assert events[0]["quality"] == 5
        assert events[0]["deck_id"] == deck.id
        assert events[0]["flashcard_id"] == cards[0].id

    async def test_refuses_another_learners_card_and_logs_nothing(self, repo, library):
        """The ownership check and the write are the same statement, so a refusal
        cannot leave a log row behind."""
        deck, cards = library
        result = await repo.apply_flashcard_review(
            cards[0].id,
            OTHER_USER,
            card_update=_card_update(interval=60),
            review=_review_payload(deck.id, quality=5, interval=60),
        )
        assert result is None
        assert (
            await repo.list_review_events(USER, since=datetime.now(UTC) - timedelta(days=1)) == []
        )
        unchanged = await repo.get_flashcard(cards[0].id, USER)
        assert unchanged.interval_days == 30

    async def test_a_streak_survives_reviewing_the_same_cards_again(self, repo, library):
        """The defect that motivated the review log.

        Grading the same three cards yesterday and again today leaves one
        ``lastReviewedAt`` each, pointing at today — so a per-card projection loses
        yesterday entirely and reports a one-day streak. Per-review rows keep both
        days.
        """
        deck, cards = library
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        for card in cards[:3]:
            await repo.apply_flashcard_review(
                card.id,
                USER,
                card_update=_card_update(interval=30, at=yesterday),
                review=_review_payload(deck.id, quality=4, interval=30, at=yesterday),
            )
        for card in cards[:3]:
            await repo.apply_flashcard_review(
                card.id,
                USER,
                card_update=_card_update(interval=60, at=now),
                review=_review_payload(deck.id, quality=5, interval=60, at=now),
            )

        stats = await repo.get_flashcard_stats(USER)
        assert stats["reviewed_total"] == 6
        assert stats["current_streak"] == 2

        # Every card now carries the same single timestamp, which is exactly why the
        # old projection could not have produced the answer above.
        timestamps = {
            (await repo.get_flashcard(card.id, USER)).last_reviewed_at.date() for card in cards[:3]
        }
        assert len(timestamps) == 1


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------


class TestForecast:
    async def test_buckets_by_day_and_separates_new_from_due(self, repo, library):
        forecast = await repo.get_review_forecast(USER, days=7)
        assert len(forecast) == 7
        # The overdue card was never reviewed, so it is new work waiting today.
        assert forecast[0] == {"date": forecast[0]["date"], "due": 0, "new": 1}
        # The three in-repetition cards are three days out.
        assert forecast[3]["due"] == 3
        assert forecast[3]["new"] == 0

    async def test_overdue_work_lands_in_the_first_bucket_not_the_past(self, repo):
        """A card due last week is work waiting today; anywhere else hides it."""
        await _card(
            repo,
            deck_id=None,
            front="long-overdue",
            interval=30,
            reviewed_days_ago=40,
            quality=4,
            next_review_in=-timedelta(days=9),
        )
        forecast = await repo.get_review_forecast(USER, days=7)
        assert forecast[0]["due"] == 1
        assert sum(day["due"] for day in forecast[1:]) == 0

    async def test_counts_nothing_scheduled_beyond_the_window(self, repo):
        await _card(
            repo,
            deck_id=None,
            front="far-future",
            interval=90,
            reviewed_days_ago=1,
            quality=4,
            next_review_in=timedelta(days=60),
        )
        forecast = await repo.get_review_forecast(USER, days=7)
        assert sum(day["due"] + day["new"] for day in forecast) == 0

    async def test_scoped_to_the_caller(self, repo, library):
        forecast = await repo.get_review_forecast(OTHER_USER, days=7)
        assert sum(day["due"] + day["new"] for day in forecast) == 0


# ---------------------------------------------------------------------------
# Mastery replay
# ---------------------------------------------------------------------------


class TestMasteryAsOf:
    async def test_reconstructs_the_interval_recorded_before_the_cutoff(self, repo, library):
        """This is what the stored SM-2 state on a review row is for."""
        deck, cards = library
        week_ago = datetime.now(UTC) - timedelta(days=7)

        # A week ago the card was short-interval; today it is mature.
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=6, at=week_ago),
            review=_review_payload(deck.id, quality=4, interval=6, at=week_ago),
        )
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40),
            review=_review_payload(deck.id, quality=5, interval=40),
        )

        as_of_now = await repo.count_mastered_by_deck_as_of(
            USER, cutoff=datetime.now(UTC) + timedelta(minutes=1)
        )
        assert as_of_now == {deck.id: 1}

        as_of_last_week = await repo.count_mastered_by_deck_as_of(
            USER, cutoff=week_ago + timedelta(minutes=1)
        )
        # Six days is not mature, so the earlier reading is zero rather than absent-
        # meaning-unknown.
        assert as_of_last_week.get(deck.id, 0) == 0

    async def test_a_card_with_no_earlier_review_is_absent(self, repo, library):
        """Absent means "no record of what it was", which the caller renders as null
        rather than as no change."""
        deck, cards = library
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40),
            review=_review_payload(deck.id, quality=5, interval=40),
        )
        as_of = await repo.count_mastered_by_deck_as_of(
            USER, cutoff=datetime.now(UTC) - timedelta(days=7)
        )
        assert as_of == {}


# ---------------------------------------------------------------------------
# Card listing, editing and deletion
# ---------------------------------------------------------------------------


class TestCardListing:
    async def test_state_filters_match_the_stats_definitions(self, repo, library):
        for state, expected in (("mastered", 2), ("learning", 1), ("new", 1), ("due", 1)):
            _, total = await repo.list_flashcards(USER, state=state)
            assert total == expected, state

    async def test_search_covers_both_sides_of_a_card(self, repo, library):
        _, by_front = await repo.list_flashcards(USER, search="MATURE-1")
        assert by_front == 1
        _, by_back = await repo.list_flashcards(USER, search="answer to new-1")
        assert by_back == 1

    async def test_paginates(self, repo, library):
        page_one, total = await repo.list_flashcards(USER, skip=0, take=3)
        page_two, _ = await repo.list_flashcards(USER, skip=3, take=3)
        assert total == 4
        assert len(page_one) == 3
        assert len(page_two) == 1
        assert {card.id for card in page_one}.isdisjoint({card.id for card in page_two})

    async def test_scoped_to_the_caller(self, repo, library):
        _, total = await repo.list_flashcards(OTHER_USER)
        assert total == 0


class TestCardWrites:
    async def test_unfiles_a_card(self, repo, library):
        _, cards = library
        updated = await repo.update_flashcard_fields(cards[0].id, USER, {"deckId": None})
        assert updated.deck_id is None

    async def test_refuses_to_edit_another_learners_card(self, repo, library):
        _, cards = library
        assert (
            await repo.update_flashcard_fields(cards[0].id, OTHER_USER, {"front": "theirs"}) is None
        )
        assert (await repo.get_flashcard(cards[0].id, USER)).front == "mature-1"

    async def test_delete_is_scoped_to_the_owner(self, repo, library):
        _, cards = library
        assert await repo.delete_flashcard(cards[0].id, OTHER_USER) is False
        assert await repo.delete_flashcard(cards[0].id, USER) is True
        assert await repo.get_flashcard(cards[0].id, USER) is None

    async def test_deleting_a_card_keeps_its_reviews(self, repo, library):
        deck, cards = library
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40),
            review=_review_payload(deck.id, quality=5, interval=40),
        )
        await repo.delete_flashcard(cards[0].id, USER)

        events = await repo.list_review_events(USER, since=datetime.now(UTC) - timedelta(days=1))
        assert len(events) == 1
        # Detached rather than removed: the review happened, the card no longer exists.
        assert events[0]["flashcard_id"] is None


class TestDeckDeletion:
    async def test_detaches_cards_instead_of_destroying_them(self, repo, library):
        """The resolution of the cascade contradiction.

        The relationship declared ``delete-orphan`` while the foreign key declared
        ``SET NULL``, so this outcome depended on which layer ran the delete.
        """
        deck, cards = library
        assert await repo.delete_deck(deck.id, USER) is True
        assert await repo.get_deck(deck.id, USER) is None

        for card in cards:
            survivor = await repo.get_flashcard(card.id, USER)
            assert survivor is not None
            assert survivor.deck_id is None

    async def test_keeps_review_history(self, repo, library):
        deck, cards = library
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40),
            review=_review_payload(deck.id, quality=5, interval=40),
        )
        await repo.delete_deck(deck.id, USER)

        events = await repo.list_review_events(USER, since=datetime.now(UTC) - timedelta(days=1))
        assert len(events) == 1
        assert events[0]["deck_id"] is None

    async def test_refuses_another_learners_deck(self, repo, library):
        deck, _ = library
        assert await repo.delete_deck(deck.id, OTHER_USER) is False
        assert await repo.get_deck(deck.id, USER) is not None


class TestGraduationEvents:
    async def test_detects_the_review_that_crossed_into_maturity(self, repo, library):
        deck, cards = library
        now = datetime.now(UTC)
        earlier = now - timedelta(days=3)

        # Below the threshold, then across it.
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=6, at=earlier),
            review=_review_payload(deck.id, quality=4, interval=6, at=earlier),
        )
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40, at=now),
            review=_review_payload(deck.id, quality=5, interval=40, at=now),
        )

        events = await repo.list_graduation_events(USER, since=now - timedelta(days=30))
        assert len(events) == 1
        assert events[0]["flashcard_id"] == cards[0].id

    async def test_does_not_repeat_a_graduation_on_every_later_review(self, repo, library):
        """Staying mature is not graduating again."""
        deck, cards = library
        now = datetime.now(UTC)
        for offset, interval in ((3, 40), (2, 60), (1, 90)):
            at = now - timedelta(days=offset)
            await repo.apply_flashcard_review(
                cards[0].id,
                USER,
                card_update=_card_update(interval=interval, at=at),
                review=_review_payload(deck.id, quality=5, interval=interval, at=at),
            )
        events = await repo.list_graduation_events(USER, since=now - timedelta(days=30))
        assert len(events) == 1

    async def test_counts_recovery_after_a_lapse(self, repo, library):
        """A card lost and rebuilt to maturity has been mastered again."""
        deck, cards = library
        now = datetime.now(UTC)
        for offset, interval in ((4, 40), (3, 1), (1, 30)):
            at = now - timedelta(days=offset)
            await repo.apply_flashcard_review(
                cards[0].id,
                USER,
                card_update=_card_update(interval=interval, at=at),
                review=_review_payload(
                    deck.id, quality=1 if interval == 1 else 5, interval=interval, at=at
                ),
            )
        events = await repo.list_graduation_events(USER, since=now - timedelta(days=30))
        assert len(events) == 2

    async def test_a_prior_review_outside_the_window_is_still_the_baseline(self, repo, library):
        """Otherwise a card looks newly graduated every time the window moves."""
        deck, cards = library
        now = datetime.now(UTC)
        long_ago = now - timedelta(days=90)

        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40, at=long_ago),
            review=_review_payload(deck.id, quality=5, interval=40, at=long_ago),
        )
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=90, at=now),
            review=_review_payload(deck.id, quality=5, interval=90, at=now),
        )
        events = await repo.list_graduation_events(USER, since=now - timedelta(days=30))
        assert events == []

    async def test_scoped_to_the_caller(self, repo, library):
        deck, cards = library
        await repo.apply_flashcard_review(
            cards[0].id,
            USER,
            card_update=_card_update(interval=40),
            review=_review_payload(deck.id, quality=5, interval=40),
        )
        assert (
            await repo.list_graduation_events(
                OTHER_USER, since=datetime.now(UTC) - timedelta(days=30)
            )
            == []
        )


class TestCardCreations:
    async def test_reports_cards_added_in_the_window(self, repo, library):
        deck, cards = library
        events = await repo.list_card_creations(USER, since=datetime.now(UTC) - timedelta(days=1))
        assert len(events) == len(cards)
        assert {event["deck_id"] for event in events} == {deck.id}

    async def test_scoped_to_the_caller(self, repo, library):
        events = await repo.list_card_creations(
            OTHER_USER, since=datetime.now(UTC) - timedelta(days=1)
        )
        assert events == []


class TestLapsingCards:
    async def test_counts_cards_at_or_over_the_threshold(self, repo):
        now = datetime.now(UTC)
        for lapses in (0, 2, 3, 5):
            await repo.create_flashcard(
                {
                    "userId": USER,
                    "front": f"lapsed-{lapses}",
                    "back": "x",
                    "intervalDays": 1,
                    "repetitionCount": 0,
                    "easeFactor": 2.5,
                    "nextReviewAt": now,
                    "lastQuality": 1,
                    "lapseCount": lapses,
                }
            )
        assert await repo.count_lapsing_flashcards(USER, min_lapses=3) == 2

    async def test_scoped_to_the_caller(self, repo, library):
        assert await repo.count_lapsing_flashcards(OTHER_USER, min_lapses=3) == 0
