"""The review-due producer: who gets reminded, and what the reminder is keyed on.

The keying is the load-bearing part — the reminder and the review-completion seam must agree on
`("deck", deck_id)` or an outcome can never be attributed — so these pin that the notification names
the deck, that the paid gate is honoured, and that an inactive account is skipped.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from src.domains.personal_learning.services import review_due_reminders


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _Result(self._rows)


def _factory(rows):
    @asynccontextmanager
    async def factory():
        yield _Session(rows)

    return factory


@pytest.fixture
def created(monkeypatch):
    """Capture create_notification calls without touching the database."""
    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)

        class _Row:
            id = "notif"

        return _Row()

    # The producer imports create_notification locally from the service module, so patch it there.
    import src.domains.notifications.service as notification_service

    monkeypatch.setattr(notification_service, "create_notification", fake_create)
    return calls


async def _eligible_true(user_id: str) -> bool:
    return True


async def _eligible_false(user_id: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_it_reminds_per_deck_keyed_on_the_deck(monkeypatch, created):
    # (user_id, deck_id, deck_title, due_count, is_active)
    rows = [("u1", "deck-bio", "Biology", 12, True)]
    monkeypatch.setattr(review_due_reminders, "get_session_factory", lambda: _factory(rows))
    monkeypatch.setattr(review_due_reminders, "_eligible", _eligible_true)

    summary = await review_due_reminders.send_review_due_reminders()

    assert summary["created"] == 1
    call = created[0]
    assert call["type"] == "learning.review_due"
    assert call["source_entity_type"] == "deck"
    assert call["source_entity_id"] == "deck-bio"
    assert call["action"] == {"version": 1, "kind": "OPEN_REVIEW", "entityId": "deck-bio"}
    # The count is in the copy, so the learner sees how much is waiting.
    assert "12" in call["title"]


@pytest.mark.asyncio
async def test_a_free_learner_is_skipped(monkeypatch, created):
    rows = [("u1", "deck-bio", "Biology", 3, True)]
    monkeypatch.setattr(review_due_reminders, "get_session_factory", lambda: _factory(rows))
    monkeypatch.setattr(review_due_reminders, "_eligible", _eligible_false)

    summary = await review_due_reminders.send_review_due_reminders()

    assert summary["created"] == 0
    assert summary["skipped"] == 1
    assert created == []


@pytest.mark.asyncio
async def test_an_inactive_account_is_skipped_before_the_paid_check(monkeypatch, created):
    rows = [("u1", "deck-bio", "Biology", 3, False)]
    monkeypatch.setattr(review_due_reminders, "get_session_factory", lambda: _factory(rows))

    # _eligible must not even be consulted for an inactive account.
    async def _explode(user_id):
        raise AssertionError("eligibility should not be checked for an inactive account")

    monkeypatch.setattr(review_due_reminders, "_eligible", _explode)

    summary = await review_due_reminders.send_review_due_reminders()

    assert summary["created"] == 0 and summary["skipped"] == 1
    assert created == []


@pytest.mark.asyncio
async def test_the_idempotency_key_names_the_deck_and_the_day(monkeypatch, created):
    rows = [("u1", "deck-bio", "Biology", 1, True)]
    monkeypatch.setattr(review_due_reminders, "get_session_factory", lambda: _factory(rows))
    monkeypatch.setattr(review_due_reminders, "_eligible", _eligible_true)

    await review_due_reminders.send_review_due_reminders()

    key = created[0]["idempotency_key"]
    assert key.startswith("review-due:deck-bio:")
    # Group key collapses a still-unread reminder rather than stacking.
    assert created[0]["group_key"] == "review-due:deck-bio"
