"""Tests for the nightly sweep that puts a passed exam in front of the learner (no DB required).

**This is the defect these tests exist for.** The sweep used to be
`mark_overdue_preparations_completed`: it selected every preparation whose `examDate` had passed and set
`status = COMPLETED`. So a learner who was 30 percent ready for an exam they missed had it recorded as
finished, and it then dropped out of `PREP_STATUSES_WORTH_A_GOAL` so it was not even a candidate for a goal
any more. A clock is not an outcome.

It now moves them to `AWAITING_REVIEW` and asks — once, then a bounded number of reminders. The status is
the part that matters and the count it returns; the asking is throttled and may be suppressed entirely by
quiet hours or held back by the learner's daily allowance, which is exactly why the ask is recorded whether
or not the message reaches them.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import exam_prep_service, prep_outcome_service

NOW = datetime.now(UTC)


def _prep(prep_id="prep-1", **overrides) -> SimpleNamespace:
    defaults = {
        "id": prep_id,
        "user_id": "user-1",
        "subject": "Statistics",
        "status": "IN_PROGRESS",
        "exam_date": NOW - timedelta(days=1),
        "review_asked_at": None,
        "review_reminders_sent": 0,
        "review_declined_at": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self, preps):
        self.preps = preps
        self.updates: list[tuple[str, dict]] = []

    async def list_preps_awaiting_review(self, *, before, limit=500, **kwargs):
        # Mirrors the real query's filters, so a test cannot pass by the fake being laxer than the
        # database: past exam, not already completed, not declined.
        return [
            prep
            for prep in self.preps
            if prep.exam_date < before
            and prep.status != "COMPLETED"
            and prep.review_declined_at is None
        ]

    async def update_exam_prep(self, prep_id, data, **kwargs):
        self.updates.append((prep_id, data))
        prep = next(p for p in self.preps if p.id == prep_id)
        for key, value in data.items():
            attr = {
                "reviewAskedAt": "review_asked_at",
                "reviewRemindersSent": "review_reminders_sent",
                "reviewDeclinedAt": "review_declined_at",
            }.get(key, key)
            setattr(prep, attr, value)
        return prep


class FakeNotifications:
    def __init__(self, suppress=False):
        self.sent: list[dict] = []
        self.suppress = suppress

    async def create_notification(self, **kwargs):
        self.sent.append(kwargs)
        # `None` is the strictest thing the real service can hand back. Since phase 5 it always returns a
        # row and defers delivery instead, so a caller that copes with `None` copes with everything.
        return None if self.suppress else SimpleNamespace(id="n1")


@pytest.fixture
def wire(monkeypatch):
    def _wire(preps, *, suppress=False):
        repo = FakeRepo(preps)
        notifications = FakeNotifications(suppress=suppress)
        monkeypatch.setattr(exam_prep_service, "repo", repo)
        # The service imports these inside the function, so the module objects are patched rather than
        # the calling module's attributes — a local `from . import x` rebinds and would ignore the latter.
        monkeypatch.setattr(
            "src.domains.personal_learning.services.notification_service.create_notification",
            notifications.create_notification,
        )
        return repo, notifications

    return _wire


class TestTheSweep:
    @pytest.mark.asyncio
    async def test_a_passed_exam_is_not_declared_complete(self, wire):
        """**The original defect, pinned.** The status must be the one that asserts nothing about how the
        exam went. `COMPLETED` is reachable only through the learner's answer."""
        prep = _prep()
        repo, _ = wire([prep])

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert prep.status == "AWAITING_REVIEW"
        assert moved == 1
        assert all(data.get("status") != "COMPLETED" for _, data in repo.updates)

    @pytest.mark.asyncio
    async def test_the_learner_is_asked(self, wire):
        prep = _prep()
        _, notifications = wire([prep])

        await exam_prep_service.mark_preparations_awaiting_review()

        assert len(notifications.sent) == 1
        assert notifications.sent[0]["type"] == "preparation_review"
        assert prep.subject in notifications.sent[0]["title"]
        assert notifications.sent[0]["action_data"]["prepId"] == prep.id

    @pytest.mark.asyncio
    async def test_a_suppressed_ask_still_counts_against_the_budget(self, wire):
        """A notification reaching nobody must not reopen the budget. Quiet hours hold a message until the
        learner's morning, their daily allowance can defer it to tomorrow, and one held too long expires
        rather than arriving stale — and before phase 5 the allowance destroyed it outright, returning
        `None`. Counting only messages that landed would let a held-back ask retry every night, turning a
        throttle into a backlog that arrives all at once. `run_weekly_check_ins` learned this the same way.

        The fake still returns `None`, which is now the strictest case rather than the ordinary one."""
        prep = _prep()
        repo, _ = wire([prep], suppress=True)

        await exam_prep_service.mark_preparations_awaiting_review()

        assert prep.review_asked_at is not None

    @pytest.mark.asyncio
    async def test_asking_stops_at_the_budget(self, wire):
        """Bounded, because this is a message arriving after a possibly bad experience. A learner who has
        ignored the budget has answered."""
        prep = _prep(
            status="AWAITING_REVIEW",
            review_asked_at=NOW - timedelta(days=3),
            review_reminders_sent=prep_outcome_service.MAX_REVIEW_REMINDERS,
        )
        _, notifications = wire([prep])

        await exam_prep_service.mark_preparations_awaiting_review()

        assert notifications.sent == []
        # Still in the awaiting state: an honest record that the exam happened and we do not know how it
        # went. The budget running out is not an outcome either.
        assert prep.status == "AWAITING_REVIEW"

    @pytest.mark.asyncio
    async def test_reminders_accumulate_one_run_at_a_time(self, wire):
        prep = _prep(status="AWAITING_REVIEW", review_asked_at=NOW - timedelta(days=1))
        wire([prep])

        await exam_prep_service.mark_preparations_awaiting_review()
        assert prep.review_reminders_sent == 1

        await exam_prep_service.mark_preparations_awaiting_review()
        assert prep.review_reminders_sent == 2

    @pytest.mark.asyncio
    async def test_the_first_ask_is_not_a_reminder(self, wire):
        """The budget counts reminders *after* the first ask, so a learner asked once has spent none of
        it. Counting the first ask as a reminder would silently cost them one third of the budget."""
        prep = _prep()
        wire([prep])

        await exam_prep_service.mark_preparations_awaiting_review()

        assert prep.review_asked_at is not None
        assert prep.review_reminders_sent == 0

    @pytest.mark.asyncio
    async def test_a_declined_preparation_is_never_asked(self, wire):
        prep = _prep(status="AWAITING_REVIEW", review_declined_at=NOW - timedelta(hours=1))
        _, notifications = wire([prep])

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert notifications.sent == []
        assert moved == 0

    @pytest.mark.asyncio
    async def test_an_answered_preparation_is_left_alone(self, wire):
        prep = _prep(status="COMPLETED")
        repo, notifications = wire([prep])

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert moved == 0
        assert notifications.sent == []
        assert repo.updates == []

    @pytest.mark.asyncio
    async def test_a_future_exam_is_left_alone(self, wire):
        prep = _prep(exam_date=NOW + timedelta(days=5))
        repo, notifications = wire([prep])

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert moved == 0
        assert notifications.sent == []
        assert repo.updates == []

    @pytest.mark.asyncio
    async def test_the_count_is_preparations_moved_not_messages_sent(self, wire):
        """They differ on every run after the first, and the status change is the part that matters."""
        already = _prep("prep-already", status="AWAITING_REVIEW", review_asked_at=NOW)
        fresh = _prep("prep-fresh")
        wire([already, fresh])

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert moved == 1

    @pytest.mark.asyncio
    async def test_one_failing_preparation_does_not_stop_the_others(self, wire, monkeypatch):
        """A sweep across every learner must not be one learner's problem."""
        bad = _prep("prep-bad")
        good = _prep("prep-good")
        repo, _ = wire([bad, good])
        original = repo.update_exam_prep

        async def exploding(prep_id, data, **kwargs):
            if prep_id == "prep-bad":
                raise RuntimeError("boom")
            return await original(prep_id, data, **kwargs)

        monkeypatch.setattr(repo, "update_exam_prep", exploding)

        moved = await exam_prep_service.mark_preparations_awaiting_review()

        assert good.status == "AWAITING_REVIEW"
        assert moved == 1
