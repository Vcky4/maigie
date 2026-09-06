"""Flashcard and deck routes: contract, ownership, and the two destructive paths.

Needs a database, so these skip when ``DATABASE_URL`` is unset. The derivations that
do not need one are in ``test_flashcard_dashboard.py``.

Every route added in this stage gets an ownership case. Deletion routes get two: that
another learner cannot reach them, and that what they destroy is exactly what was
documented — a deleted deck must leave its cards behind, and a deleted card must leave
its review history behind.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/v1/learning"


async def _second_user(client: AsyncClient) -> dict[str, str]:
    """Sign up, activate and log in a second learner, for cross-user checks."""
    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    email = f"other_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "name": "Other Learner"},
    )
    if signup.status_code not in (200, 201):
        pytest.skip(f"Signup failed: {signup.status_code}")

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(sa_update(User).where(User.email == email).values(is_active=True))
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login/json", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    token = login.json().get("access_token") or login.json().get("accessToken")
    return {"Authorization": f"Bearer {token}"}


async def _create_deck(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = {"title": "Algorithms", "subject": "Computer Science", **overrides}
    response = await client.post(f"{BASE}/decks", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_card(
    client: AsyncClient, headers: dict[str, str], *, deck_id: str | None = None, front="Q", back="A"
) -> dict:
    payload = {"front": front, "back": back}
    if deck_id is not None:
        payload["deckId"] = deck_id
    response = await client.post(f"{BASE}/flashcards", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------


class TestDeckContract:
    async def test_create_persists_the_fields_the_create_form_collects(
        self, client: AsyncClient, auth_headers
    ):
        """Subject, colour and daily pace had nowhere to go before this stage."""
        deck = await _create_deck(
            client,
            auth_headers,
            subject="Mathematics",
            accent="orange",
            dailyGoal=15,
            description="Probability rules",
        )
        assert deck["subject"] == "Mathematics"
        assert deck["accent"] == "orange"
        assert deck["dailyGoal"] == 15

        detail = await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        assert detail.status_code == 200
        assert detail.json()["subject"] == "Mathematics"

    async def test_rejects_an_accent_it_does_not_define(self, client: AsyncClient, auth_headers):
        response = await client.post(
            f"{BASE}/decks", json={"title": "X", "accent": "chartreuse"}, headers=auth_headers
        )
        # 400, not FastAPI's default 422: `validation_error_handler` is registered
        # app-wide and normalises validation failures to 400 with a stable code.
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_new_deck_reports_empty_aggregates_and_learning_status(
        self, client: AsyncClient, auth_headers
    ):
        deck = await _create_deck(client, auth_headers)
        assert deck["cardCount"] == 0
        assert deck["dueCount"] == 0
        assert deck["masteredCount"] == 0
        assert deck["masteryPercent"] == 0
        # Null, not 0%: an unreviewed deck has no recall rather than a failing one.
        assert deck["recallPercent"] is None
        assert deck["lastReviewedAt"] is None
        assert deck["status"] == "learning"

    async def test_detail_counts_cards(self, client: AsyncClient, auth_headers):
        deck = await _create_deck(client, auth_headers)
        await _create_card(client, auth_headers, deck_id=deck["id"])
        await _create_card(client, auth_headers, deck_id=deck["id"], front="Q2")

        detail = await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        assert detail.json()["cardCount"] == 2

    async def test_patch_updates_only_what_was_sent(self, client: AsyncClient, auth_headers):
        deck = await _create_deck(client, auth_headers, subject="Mathematics")
        response = await client.patch(
            f"{BASE}/decks/{deck['id']}", json={"title": "Renamed"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"
        assert response.json()["subject"] == "Mathematics"

    async def test_patch_can_clear_a_field_explicitly(self, client: AsyncClient, auth_headers):
        """An explicit null clears; an omitted key would not."""
        deck = await _create_deck(client, auth_headers, subject="Mathematics")
        response = await client.patch(
            f"{BASE}/decks/{deck['id']}", json={"subject": None}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["subject"] is None

    async def test_detail_patch_and_delete_are_unreachable_for_another_learner(
        self, client: AsyncClient, auth_headers
    ):
        deck = await _create_deck(client, auth_headers)
        intruder = await _second_user(client)

        assert (await client.get(f"{BASE}/decks/{deck['id']}", headers=intruder)).status_code == 404
        assert (
            await client.patch(
                f"{BASE}/decks/{deck['id']}", json={"title": "Theirs"}, headers=intruder
            )
        ).status_code == 404
        assert (
            await client.delete(f"{BASE}/decks/{deck['id']}", headers=intruder)
        ).status_code == 404
        # And it is still the owner's, unchanged.
        owner_view = await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        assert owner_view.status_code == 200
        assert owner_view.json()["title"] == deck["title"]

    async def test_cannot_file_a_card_into_another_learners_deck(
        self, client: AsyncClient, auth_headers
    ):
        """The foreign key only checks the deck exists, not who owns it."""
        deck = await _create_deck(client, auth_headers)
        intruder = await _second_user(client)
        response = await client.post(
            f"{BASE}/flashcards",
            json={"front": "Q", "back": "A", "deckId": deck["id"]},
            headers=intruder,
        )
        assert response.status_code == 404

        detail = await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        assert detail.json()["cardCount"] == 0


class TestDeckDeletionDetachesCards:
    async def test_cards_survive_their_deck(self, client: AsyncClient, auth_headers):
        """The documented semantic: a deck is a container, not an owner.

        The ORM relationship used to declare ``delete-orphan`` while the foreign key
        declared ``SET NULL``, so this outcome depended on which layer ran the delete.
        """
        deck = await _create_deck(client, auth_headers)
        card = await _create_card(client, auth_headers, deck_id=deck["id"])

        response = await client.delete(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        assert response.status_code == 204

        assert (
            await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)
        ).status_code == 404

        survivor = await client.get(f"{BASE}/flashcards/{card['id']}", headers=auth_headers)
        assert survivor.status_code == 200
        assert survivor.json()["deckId"] is None


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class TestFlashcardListing:
    async def test_list_is_paginated_and_scoped_to_the_caller(
        self, client: AsyncClient, auth_headers
    ):
        await _create_card(client, auth_headers, front="Mine")
        intruder = await _second_user(client)
        await _create_card(client, intruder, front="Theirs")

        response = await client.get(f"{BASE}/flashcards", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert {"items", "total", "page", "pageSize", "pages"} <= set(body)
        assert all(item["front"] != "Theirs" for item in body["items"])

    async def test_search_matches_front_and_back(self, client: AsyncClient, auth_headers):
        await _create_card(client, auth_headers, front="Dijkstra", back="shortest path")
        await _create_card(client, auth_headers, front="Quicksort", back="divide and conquer")

        by_front = await client.get(
            f"{BASE}/flashcards", params={"search": "dijkstra"}, headers=auth_headers
        )
        assert [item["front"] for item in by_front.json()["items"]] == ["Dijkstra"]

        by_back = await client.get(
            f"{BASE}/flashcards", params={"search": "conquer"}, headers=auth_headers
        )
        assert [item["front"] for item in by_back.json()["items"]] == ["Quicksort"]

    async def test_new_state_selects_never_reviewed_cards(self, client: AsyncClient, auth_headers):
        card = await _create_card(client, auth_headers)
        response = await client.get(
            f"{BASE}/flashcards", params={"state": "new"}, headers=auth_headers
        )
        assert card["id"] in {item["id"] for item in response.json()["items"]}

    async def test_rejects_a_state_it_does_not_define(self, client: AsyncClient, auth_headers):
        response = await client.get(
            f"{BASE}/flashcards", params={"state": "forgotten"}, headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_due_sort_orders_the_page_and_survives_paging(
        self, client: AsyncClient, auth_headers
    ):
        """Ordering is the server's, because a page boundary needs a defined order.

        Also guards the tie-break: these cards are created in one loop and can share a
        timestamp, and without a secondary key a card can land on two pages or none.
        """
        deck = await _create_deck(client, auth_headers)
        for index in range(5):
            await _create_card(client, auth_headers, deck_id=deck["id"], front=f"Q{index}")

        collected: list[str] = []
        for page in (1, 2, 3):
            response = await client.get(
                f"{BASE}/flashcards",
                params={"deckId": deck["id"], "sort": "due", "page": page, "pageSize": 2},
                headers=auth_headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["total"] == 5
            due_dates = [item["nextReviewAt"] for item in body["items"]]
            assert due_dates == sorted(due_dates)
            collected.extend(item["id"] for item in body["items"])

        assert len(collected) == 5
        assert len(set(collected)) == 5, "paging lost or repeated a card"

    async def test_rejects_a_sort_it_does_not_define(self, client: AsyncClient, auth_headers):
        response = await client.get(
            f"{BASE}/flashcards", params={"sort": "alphabetical"}, headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_deck_filter_scopes_the_list(self, client: AsyncClient, auth_headers):
        deck = await _create_deck(client, auth_headers)
        filed = await _create_card(client, auth_headers, deck_id=deck["id"], front="Filed")
        await _create_card(client, auth_headers, front="Unfiled")

        response = await client.get(
            f"{BASE}/flashcards", params={"deckId": deck["id"]}, headers=auth_headers
        )
        assert [item["id"] for item in response.json()["items"]] == [filed["id"]]


class TestFlashcardUpdate:
    async def test_edits_text_without_touching_the_schedule(
        self, client: AsyncClient, auth_headers
    ):
        """SM-2 state is not client-editable; a second scheduler would drift."""
        card = await _create_card(client, auth_headers)
        response = await client.patch(
            f"{BASE}/flashcards/{card['id']}",
            json={"front": "Edited", "intervalDays": 999, "easeFactor": 9.9},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["front"] == "Edited"
        assert body["intervalDays"] == card["intervalDays"]
        assert body["easeFactor"] == card["easeFactor"]

    async def test_moves_a_card_between_decks(self, client: AsyncClient, auth_headers):
        source = await _create_deck(client, auth_headers, title="Source")
        target = await _create_deck(client, auth_headers, title="Target")
        card = await _create_card(client, auth_headers, deck_id=source["id"])

        response = await client.patch(
            f"{BASE}/flashcards/{card['id']}",
            json={"deckId": target["id"]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["deckId"] == target["id"]

    async def test_explicit_null_unfiles_a_card(self, client: AsyncClient, auth_headers):
        deck = await _create_deck(client, auth_headers)
        card = await _create_card(client, auth_headers, deck_id=deck["id"])
        response = await client.patch(
            f"{BASE}/flashcards/{card['id']}", json={"deckId": None}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["deckId"] is None

    async def test_cannot_move_a_card_into_another_learners_deck(
        self, client: AsyncClient, auth_headers
    ):
        card = await _create_card(client, auth_headers)
        intruder = await _second_user(client)
        their_deck = await _create_deck(client, intruder, title="Theirs")

        response = await client.patch(
            f"{BASE}/flashcards/{card['id']}",
            json={"deckId": their_deck["id"]},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_another_learner_cannot_edit_or_delete(self, client: AsyncClient, auth_headers):
        card = await _create_card(client, auth_headers)
        intruder = await _second_user(client)

        assert (
            await client.get(f"{BASE}/flashcards/{card['id']}", headers=intruder)
        ).status_code == 404
        assert (
            await client.patch(
                f"{BASE}/flashcards/{card['id']}", json={"front": "X"}, headers=intruder
            )
        ).status_code == 404
        assert (
            await client.delete(f"{BASE}/flashcards/{card['id']}", headers=intruder)
        ).status_code == 404


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


async def _count_reviews(user_email_token: dict[str, str] | None = None) -> int:
    from sqlalchemy import func, select

    from src.domains.personal_learning.db_models import FlashcardReview
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(func.count()).select_from(FlashcardReview))
        return result.scalar() or 0


class TestReviewLogging:
    async def test_a_grade_writes_exactly_one_review_row(self, client: AsyncClient, auth_headers):
        card = await _create_card(client, auth_headers)
        before = await _count_reviews()

        response = await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 4}, headers=auth_headers
        )
        assert response.status_code == 200
        assert await _count_reviews() == before + 1

    async def test_the_row_records_the_state_the_grade_produced(
        self, client: AsyncClient, auth_headers
    ):
        """Interval and ease are stored so an earlier mastery reading can be replayed."""
        from sqlalchemy import select

        from src.domains.personal_learning.db_models import FlashcardReview
        from src.shared.database.session import get_session_factory

        deck = await _create_deck(client, auth_headers)
        card = await _create_card(client, auth_headers, deck_id=deck["id"])
        graded = await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 5}, headers=auth_headers
        )
        assert graded.status_code == 200

        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    select(FlashcardReview).where(FlashcardReview.flashcard_id == card["id"])
                )
            ).scalar_one()
        assert row.quality == 5
        assert row.was_lapse is False
        assert row.interval_days == graded.json()["intervalDays"]
        assert row.deck_id == deck["id"]

    async def test_a_lapse_is_flagged_as_one(self, client: AsyncClient, auth_headers):
        from sqlalchemy import select

        from src.domains.personal_learning.db_models import FlashcardReview
        from src.shared.database.session import get_session_factory

        card = await _create_card(client, auth_headers)
        await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 1}, headers=auth_headers
        )

        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    select(FlashcardReview).where(FlashcardReview.flashcard_id == card["id"])
                )
            ).scalar_one()
        assert row.was_lapse is True

    async def test_another_learners_card_cannot_be_graded_or_logged(
        self, client: AsyncClient, auth_headers
    ):
        card = await _create_card(client, auth_headers)
        intruder = await _second_user(client)
        before = await _count_reviews()

        response = await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 4}, headers=intruder
        )
        assert response.status_code == 404
        # The refusal must not have logged anything either.
        assert await _count_reviews() == before

    async def test_deleting_a_card_keeps_its_reviews(self, client: AsyncClient, auth_headers):
        """A review that happened is a fact about the learner's week.

        Cascading would let deleting a card retract a streak that was already earned.
        """
        card = await _create_card(client, auth_headers)
        await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 4}, headers=auth_headers
        )
        after_review = await _count_reviews()

        assert (
            await client.delete(f"{BASE}/flashcards/{card['id']}", headers=auth_headers)
        ).status_code == 204
        assert (
            await client.get(f"{BASE}/flashcards/{card['id']}", headers=auth_headers)
        ).status_code == 404
        assert await _count_reviews() == after_review

    async def test_grading_reports_a_review_count_and_a_streak(
        self, client: AsyncClient, auth_headers
    ):
        card = await _create_card(client, auth_headers)
        await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 4}, headers=auth_headers
        )
        stats = await client.get(f"{BASE}/flashcards/stats", headers=auth_headers)
        assert stats.status_code == 200
        body = stats.json()
        assert body["reviewedTotal"] >= 1
        assert body["currentStreak"] >= 1
        assert body["recallPercent"] is not None


# ---------------------------------------------------------------------------
# Due queue and stats scoping
# ---------------------------------------------------------------------------


class TestDueQueue:
    async def test_limit_bounds_the_queue(self, client: AsyncClient, auth_headers):
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update as sa_update

        from src.domains.personal_learning.db_models import Flashcard
        from src.shared.database.session import get_session_factory

        cards = [await _create_card(client, auth_headers, front=f"Q{i}") for i in range(3)]
        # New cards are scheduled a day out, so make them due. `values()` on an ORM
        # update takes the mapped attribute name, not the camelCase column name.
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                sa_update(Flashcard)
                .where(Flashcard.id.in_([card["id"] for card in cards]))
                .values(next_review_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await session.commit()

        unbounded = await client.get(f"{BASE}/flashcards/due", headers=auth_headers)
        assert len(unbounded.json()) >= 3

        bounded = await client.get(
            f"{BASE}/flashcards/due", params={"limit": 2}, headers=auth_headers
        )
        assert len(bounded.json()) == 2

    async def test_deck_filter_scopes_the_queue(self, client: AsyncClient, auth_headers):
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update as sa_update

        from src.domains.personal_learning.db_models import Flashcard
        from src.shared.database.session import get_session_factory

        deck = await _create_deck(client, auth_headers)
        filed = await _create_card(client, auth_headers, deck_id=deck["id"])
        unfiled = await _create_card(client, auth_headers, front="Unfiled")

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                sa_update(Flashcard)
                .where(Flashcard.id.in_([filed["id"], unfiled["id"]]))
                .values(next_review_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await session.commit()

        scoped = await client.get(
            f"{BASE}/flashcards/due", params={"deckId": deck["id"]}, headers=auth_headers
        )
        assert [item["id"] for item in scoped.json()] == [filed["id"]]

    async def test_due_is_not_shadowed_by_the_card_detail_route(
        self, client: AsyncClient, auth_headers
    ):
        """`/flashcards/due` must not be read as card id "due"."""
        response = await client.get(f"{BASE}/flashcards/due", headers=auth_headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestStatsScoping:
    async def test_deck_scoped_stats_count_only_that_deck(self, client: AsyncClient, auth_headers):
        deck = await _create_deck(client, auth_headers)
        await _create_card(client, auth_headers, deck_id=deck["id"])
        await _create_card(client, auth_headers, front="Elsewhere")

        scoped = await client.get(
            f"{BASE}/flashcards/stats", params={"deckId": deck["id"]}, headers=auth_headers
        )
        assert scoped.json()["total"] == 1

        overall = await client.get(f"{BASE}/flashcards/stats", headers=auth_headers)
        assert overall.json()["total"] >= 2

    async def test_states_partition_the_library(self, client: AsyncClient, auth_headers):
        await _create_card(client, auth_headers)
        stats = (await client.get(f"{BASE}/flashcards/stats", headers=auth_headers)).json()
        assert stats["masteredCount"] + stats["learningCount"] + stats["newCount"] == stats["total"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestFlashcardsDashboard:
    async def test_requires_authentication(self, client: AsyncClient):
        response = await client.get(f"{BASE}/flashcards/dashboard")
        assert response.status_code in (401, 403)

    async def test_empty_account_is_reported_as_empty_not_as_zeroes_with_content(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["decks"] == []
        assert body["activity"] == []
        assert body["deckMastery"] == []
        assert body["stats"]["totalCards"] == 0
        # Genuinely unknown, rather than 0%.
        assert body["review"]["retentionPercent"] is None
        assert body["stats"]["masteredPercent"] is None
        assert body["meta"]["hasReviewHistory"] is False
        assert body["insight"]["kind"] == "empty_library"

    async def test_activity_reports_card_creation_before_any_review(
        self, client: AsyncClient, auth_headers
    ):
        """Adding cards is progress, so the feed must not be empty until the first review."""
        deck = await _create_deck(client, auth_headers)
        await _create_card(client, auth_headers, deck_id=deck["id"])

        body = (await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)).json()
        kinds = {entry["kind"] for entry in body["activity"]}
        assert kinds == {"created"}
        created = body["activity"][0]
        assert created["deckTitle"] == deck["title"]
        assert created["cardCount"] == 1
        assert created["recallPercent"] is None

    async def test_forecast_is_bounded_and_labelled(self, client: AsyncClient, auth_headers):
        response = await client.get(
            f"{BASE}/flashcards/dashboard", params={"forecastDays": 7}, headers=auth_headers
        )
        forecast = response.json()["forecast"]
        assert len(forecast) == 7
        assert forecast[0]["isToday"] is True
        assert all(day["due"] >= 0 and day["newCards"] >= 0 for day in forecast)

    async def test_rejects_an_unbounded_forecast(self, client: AsyncClient, auth_headers):
        response = await client.get(
            f"{BASE}/flashcards/dashboard", params={"forecastDays": 400}, headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_reports_the_zone_its_day_figures_were_computed_in(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)
        timezone = response.json()["meta"]["timezone"]
        assert "name" in timezone
        # A learner who has never been asked must not be reported as being in UTC.
        assert timezone["isKnown"] is False

    async def test_day_figures_use_the_learners_zone_once_it_is_known(
        self, client: AsyncClient, auth_headers
    ):
        """Exercises the Postgres-only zone conversion, which the default path skips.

        Day grouping uses `timezone(name, timestamptz)`, which exists in Postgres and
        not in SQLite, and it is only reached when the learner's zone is a fact rather
        than a fallback. Every other test here runs as a learner who has never been
        asked, so without this one the zone-aware query would ship unexecuted.
        """
        recorded = await client.put(
            "/api/v1/users/me/timezone",
            json={"timezone": "Pacific/Auckland"},
            headers=auth_headers,
        )
        assert recorded.status_code == 200, recorded.text

        deck = await _create_deck(client, auth_headers)
        card = await _create_card(client, auth_headers, deck_id=deck["id"])
        graded = await client.post(
            f"{BASE}/flashcards/{card['id']}/review", json={"quality": 4}, headers=auth_headers
        )
        assert graded.status_code == 200

        response = await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta"]["timezone"] == {"name": "Pacific/Auckland", "isKnown": True}
        # The streak, weekly count and activity feed all come from the zone-grouped
        # query, so a non-zero reading proves it ran rather than silently erroring.
        assert body["review"]["reviewStreak"] == 1
        assert body["review"]["reviewedThisWeek"] == 1
        assert body["meta"]["hasReviewHistory"] is True
        assert any(entry["kind"] == "reviewed" for entry in body["activity"])
        # And the forecast is bucketed on Auckland days, not UTC days.
        assert body["forecast"][0]["isToday"] is True

    async def test_a_new_card_appears_in_the_forecast_and_the_counts(
        self, client: AsyncClient, auth_headers
    ):
        deck = await _create_deck(client, auth_headers)
        await _create_card(client, auth_headers, deck_id=deck["id"])

        body = (await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)).json()
        assert body["stats"]["totalCards"] == 1
        assert body["stats"]["newCards"] == 1
        assert sum(day["newCards"] for day in body["forecast"]) == 1
        assert [deck_row["id"] for deck_row in body["decks"]] == [deck["id"]]

    async def test_deck_figures_agree_with_the_deck_route(self, client: AsyncClient, auth_headers):
        """One composed read must not contradict the per-deck read beside it."""
        deck = await _create_deck(client, auth_headers)
        await _create_card(client, auth_headers, deck_id=deck["id"])

        dashboard = (await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)).json()
        detail = (await client.get(f"{BASE}/decks/{deck['id']}", headers=auth_headers)).json()
        row = next(item for item in dashboard["decks"] if item["id"] == deck["id"])
        for field in ("cardCount", "dueCount", "masteredCount", "masteryPercent", "status"):
            assert row[field] == detail[field]

    async def test_shows_nothing_belonging_to_another_learner(
        self, client: AsyncClient, auth_headers
    ):
        await _create_deck(client, auth_headers, title="Mine")
        intruder = await _second_user(client)
        await _create_deck(client, intruder, title="Theirs")

        body = (await client.get(f"{BASE}/flashcards/dashboard", headers=auth_headers)).json()
        assert [deck["title"] for deck in body["decks"]] == ["Mine"]
