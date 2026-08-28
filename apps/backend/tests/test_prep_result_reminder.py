"""The sweep that asks for a mark on an exam already reviewed (no DB required).

**This is the ask readiness calibration is waiting on.** `experienceRating` is a 1–5 self-report and a
readiness *percentage* cannot honestly be scored against one; `resultValue` is the only objective outcome
signal the system can obtain. It has been on both clients since the review shipped and nothing ever pointed at
it, so the population carrying a mark was whoever happened to volunteer — measured at one, and only because it
was requested by hand.

Every case below corresponds to a way this could go wrong for a learner, not to a line of code:

 - **Asking about an exam they did not sit.** A missed or cancelled sitting has no mark, and a `postponed` one
   is superseded by a later sitting with its own review. Asking anyway is asking about something that did not
   happen.
 - **Asking before the mark could possibly exist.** An ask that arrives before the result does teaches the
   learner this reminder is noise, after which the second is ignored too. Erring late costs a delay; erring
   early costs the channel.
 - **Asking more times than the budget allows.** The learner may simply not have a mark, and no number of
   reminders produces one.
 - **Letting a suppressed notification refund the budget.** Quiet hours and the daily allowance can stop a
   message; treating that as "not asked" would let a learner in permanent quiet hours be asked forever.
 - **One learner's failure stopping the sweep.**
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning.services import prep_outcome_service  # noqa: E402

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
#: Long enough ago that the first ask is due.
ANSWERED = NOW - timedelta(days=20)


def _outcome(outcome_id="out-1", **overrides) -> SimpleNamespace:
    defaults = {
        "id": outcome_id,
        "prep_id": "prep-1",
        "user_id": "user-1",
        "attended": "sat",
        "answered_at": ANSWERED,
        "result_value": None,
        "result_asked_at": None,
        "result_reminders_sent": 0,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    """Mirrors the real query's filters, so a test cannot pass by the fake being laxer than the database."""

    def __init__(self, outcomes, *, prep=SimpleNamespace(id="prep-1", subject="Statistics")):
        self.outcomes = outcomes
        self.prep = prep
        self.reminders: list[tuple[str, datetime]] = []
        self.find_raises = False

    async def list_outcomes_awaiting_a_result(
        self, *, answered_before, asked_before, max_reminders, limit=500, **kwargs
    ):
        return [
            o
            for o in self.outcomes
            if o.result_value is None
            and o.attended == "sat"
            and o.answered_at < answered_before
            and (o.result_reminders_sent or 0) < max_reminders
            and (o.result_asked_at is None or o.result_asked_at < asked_before)
        ]

    async def find_exam_prep(self, prep_id, user_id, **kwargs):
        if self.find_raises:
            raise RuntimeError("prep read failed")
        return self.prep

    async def record_result_reminder(self, outcome_id, *, now, **kwargs):
        self.reminders.append((outcome_id, now))


@pytest.fixture
def wire(monkeypatch):
    def _wire(outcomes, *, suppress=False, notify_raises=False, **repo_kwargs):
        repo = FakeRepo(outcomes, **repo_kwargs)
        sent: list[dict] = []

        async def create_notification(**kwargs):
            if notify_raises:
                raise RuntimeError("notification exploded")
            sent.append(kwargs)
            return None if suppress else SimpleNamespace(id="n1")

        monkeypatch.setattr(prep_outcome_service, "repo", repo)
        monkeypatch.setattr(
            "src.domains.personal_learning.services.notification_service.create_notification",
            create_notification,
        )
        return repo, sent

    return _wire


class TestWhoGetsAsked:
    @pytest.mark.asyncio
    async def test_a_sat_exam_with_no_mark_is_asked_about(self, wire):
        repo, sent = wire([_outcome()])

        asked = await prep_outcome_service.remind_about_missing_results(now=NOW)

        assert asked == 1
        assert len(sent) == 1
        assert sent[0]["type"] == "preparation_result"
        assert "Statistics" in sent[0]["title"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("attended", ["missed", "cancelled", "postponed"])
    async def test_an_exam_that_was_not_sat_is_never_asked_about(self, wire, attended):
        """There is no mark for an exam nobody sat, and a postponed sitting has a later review of its own."""
        repo, sent = wire([_outcome(attended=attended)])

        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_recorded_mark_ends_the_asking(self, wire):
        repo, sent = wire([_outcome(result_value=72.0)])

        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0
        assert sent == []

    @pytest.mark.asyncio
    async def test_nothing_is_asked_before_the_mark_could_exist(self, wire):
        """The day after the exam there is no result. Asking then spends the channel's credibility."""
        just_answered = _outcome(answered_at=NOW - timedelta(days=1))
        repo, sent = wire([just_answered])

        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0
        assert sent == []

    @pytest.mark.asyncio
    async def test_the_first_ask_lands_once_the_wait_has_passed(self, wire):
        """Boundary: exactly at the threshold is not yet due; a day past it is."""
        at_threshold = _outcome(
            answered_at=NOW - timedelta(days=prep_outcome_service.RESULT_FIRST_ASK_DAYS)
        )
        _, sent = wire([at_threshold])
        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0

        past_threshold = _outcome(
            answered_at=NOW - timedelta(days=prep_outcome_service.RESULT_FIRST_ASK_DAYS + 1)
        )
        _, sent = wire([past_threshold])
        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 1


class TestTheBudget:
    def test_the_cap_is_two(self):
        """Asserted as a **literal**, deliberately.

        The behaviour test below used `result_reminders_sent=MAX_RESULT_REMINDERS`, which reads the constant
        it is testing — so raising the cap to 99 raised the fixture with it and the test passed. A mutation
        proved it. Pinning the number here and using a literal there makes a change to either fail.
        """
        assert prep_outcome_service.MAX_RESULT_REMINDERS == 2

    @pytest.mark.asyncio
    async def test_the_cap_is_respected(self, wire):
        spent = _outcome(result_reminders_sent=2, result_asked_at=NOW - timedelta(days=60))
        repo, sent = wire([spent])

        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_second_ask_waits_for_the_interval(self, wire):
        recent = _outcome(result_reminders_sent=1, result_asked_at=NOW - timedelta(days=2))
        _, sent = wire([recent])
        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0

        due = _outcome(
            result_reminders_sent=1,
            result_asked_at=NOW - timedelta(days=prep_outcome_service.RESULT_ASK_INTERVAL_DAYS + 1),
        )
        _, sent = wire([due])
        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 1

    @pytest.mark.asyncio
    async def test_a_suppressed_notification_still_spends_the_budget(self, wire):
        """Quiet hours and the daily allowance can stop the message.

        The budget bounds how often we **ask**. Refunding it when delivery is declined would let a learner
        whose quiet hours never open be asked indefinitely — the failure mode the review ask's own budget
        exists to prevent.
        """
        repo, sent = wire([_outcome()], suppress=True)

        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 1
        assert len(repo.reminders) == 1


class TestFailuresAreContained:
    @pytest.mark.asyncio
    async def test_a_deleted_preparation_is_skipped_cleanly(self, wire, caplog):
        """The outcome row survives its preparation, so there may be nothing left to name in the message.

        **Asserting the absence of a warning is the point.** Without the `if prep is None` guard the code
        still produced the same visible outcome — no notification, nothing recorded — because `prep.id` raised
        and the per-outcome `except` swallowed it. A mutation removing the guard passed every other assertion
        here. The difference between "skipped, as designed" and "crashed, and we hid it" is only visible in
        the log, so that is what this checks.
        """
        repo, sent = wire([_outcome()], prep=None)

        with caplog.at_level("WARNING"):
            assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0

        assert sent == []
        # Nothing recorded either: an ask that was never made must not spend the budget.
        assert repo.reminders == []
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_sweep(self, wire):
        repo, sent = wire([_outcome("out-1"), _outcome("out-2")], notify_raises=True)

        # Both fail, and the sweep returns rather than propagating.
        assert await prep_outcome_service.remind_about_missing_results(now=NOW) == 0
        assert repo.reminders == []

    @pytest.mark.asyncio
    async def test_the_ask_is_recorded_before_it_is_counted(self, wire):
        """The counter is what bounds the sweep, so it must be written for every ask attempted."""
        repo, sent = wire([_outcome("out-1"), _outcome("out-2")])

        asked = await prep_outcome_service.remind_about_missing_results(now=NOW)

        assert asked == 2
        assert [outcome_id for outcome_id, _ in repo.reminders] == ["out-1", "out-2"]
        assert all(stamped == NOW for _, stamped in repo.reminders)


class TestTheMessage:
    @pytest.mark.asyncio
    async def test_it_points_at_the_review_where_the_field_is(self, wire):
        _, sent = wire([_outcome()])

        await prep_outcome_service.remind_about_missing_results(now=NOW)

        action = sent[0]["action_data"]
        assert action["target"] == "exam-prep"
        assert action["prepId"] == "prep-1"
        assert action["tab"] == "review"

    @pytest.mark.asyncio
    async def test_it_never_outranks_the_review_ask_itself(self, wire):
        """The review completes a preparation and expires as memory fades. This is a nice-to-have about a
        number the learner either has or does not, so it must sit below both that and the daily plan."""
        _, sent = wire([_outcome()])

        await prep_outcome_service.remind_about_missing_results(now=NOW)

        assert sent[0]["priority"] > 3

    @pytest.mark.asyncio
    async def test_it_says_the_ask_is_optional(self, wire):
        """A mark the learner does not have is not a task they have failed to do."""
        _, sent = wire([_outcome()])

        await prep_outcome_service.remind_about_missing_results(now=NOW)

        assert "skip" in sent[0]["body"].lower()
