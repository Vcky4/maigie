"""Tests for the post-exam review — how a preparation actually completes (no DB required).

The thing being pinned is that **nothing is inferred from a date**. Before this, a nightly sweep set
`COMPLETED` on every preparation whose `examDate` had passed, so a learner 30 percent ready for an exam
they missed had it recorded as finished. Every test below is ultimately about that: the clock moves a
preparation into `AWAITING_REVIEW`, and only the learner's answer moves it to `COMPLETED`.

The second thing pinned is the calibration snapshot. `progress_percent` is a prediction that has been
shown to learners and used to gate goal progress without ever being compared against an outcome, because
no outcome existed. It is copied onto the answer, and it is copied as `None` rather than `0` when there
was nothing to measure — a readiness of zero and an absence of readiness are different claims.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import prep_outcome_service
from src.shared.exceptions import NotFoundError, ValidationError

OWNER = "user-owner"
INTRUDER = "user-intruder"
NOW = datetime.now(UTC)


def _prep(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "prep-1",
        "user_id": OWNER,
        "subject": "Statistics",
        "status": "AWAITING_REVIEW",
        # Yesterday: the exam has happened.
        "exam_date": NOW - timedelta(days=1),
        "target_readiness": 85,
        "review_asked_at": NOW - timedelta(hours=12),
        "review_reminders_sent": 0,
        "review_declined_at": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _progress(**overrides):
    """A `PrepProgress`-shaped stand-in. Only the four fields the snapshot copies are read."""
    defaults = {
        "progress_percent": 70.0,
        "average_mastery_percent": 66.0,
        "topics_total": 10,
        "topics_strong": 7,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self, prep: SimpleNamespace | None = None):
        self.prep = prep
        self.outcomes: dict[tuple[str, datetime], SimpleNamespace] = {}
        self.prep_updates: list[dict] = []

    async def find_exam_prep(self, prep_id, user_id, **kwargs):
        if self.prep is None or self.prep.id != prep_id or self.prep.user_id != user_id:
            return None
        return self.prep

    async def upsert_prep_outcome(self, *, prep_id, exam_date, values, **kwargs):
        key = (prep_id, exam_date)
        existing = self.outcomes.get(key)
        if existing is not None:
            for field, value in values.items():
                setattr(existing, field, value)
            return existing
        row = SimpleNamespace(prep_id=prep_id, exam_date=exam_date, **values)
        self.outcomes[key] = row
        return row

    async def find_prep_outcome(self, *, prep_id, exam_date, **kwargs):
        return self.outcomes.get((prep_id, exam_date))

    async def list_prep_outcomes(self, prep_id, **kwargs):
        rows = [row for key, row in self.outcomes.items() if key[0] == prep_id]
        return sorted(rows, key=lambda row: row.exam_date)

    async def update_exam_prep(self, prep_id, data, **kwargs):
        self.prep_updates.append(data)
        for key, value in data.items():
            attr = {
                "examDate": "exam_date",
                "reviewAskedAt": "review_asked_at",
                "reviewRemindersSent": "review_reminders_sent",
                "reviewDeclinedAt": "review_declined_at",
            }.get(key, key)
            setattr(self.prep, attr, value)
        return self.prep


@pytest.fixture
def repo(monkeypatch):
    """A repo with one preparation whose exam was yesterday, and no goal or feed side effects.

    `_resolve_linked_goal` and `_record_activity` are stubbed out rather than faked: both already contain
    their own `try/except` and their behaviour is not what these tests are about. Stubbing them means a
    failure in one shows up as a failure in its own test rather than as noise in twenty.
    """
    fake = FakeRepo(_prep())
    monkeypatch.setattr(prep_outcome_service, "repo", fake)

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(prep_outcome_service, "_resolve_linked_goal", _noop)
    monkeypatch.setattr(prep_outcome_service, "_record_activity", _noop)

    async def _readiness(prep_id):
        return {
            "readiness_percent": 70.0,
            "average_mastery_percent": 66.0,
            "topics_total": 10,
            "topics_strong": 7,
        }

    monkeypatch.setattr(prep_outcome_service, "_readiness_at_answer", _readiness)
    return fake


# ---------------------------------------------------------------------------
# is_awaiting_review
# ---------------------------------------------------------------------------


class TestAwaitingReview:
    def test_waits_once_the_exam_has_passed(self):
        assert prep_outcome_service.is_awaiting_review(_prep()) is True

    def test_does_not_wait_before_the_exam(self):
        """There is nothing to review until it has happened."""
        assert (
            prep_outcome_service.is_awaiting_review(_prep(exam_date=NOW + timedelta(days=3)))
            is False
        )

    def test_does_not_wait_once_completed(self):
        assert prep_outcome_service.is_awaiting_review(_prep(status="COMPLETED")) is False

    def test_a_dismissal_stops_the_waiting(self):
        """**A dismissal is an answer.** Continuing to ask someone who said no is the failure the
        reminder budget exists to prevent, and declining must end it outright rather than decrement it."""
        assert prep_outcome_service.is_awaiting_review(_prep(review_declined_at=NOW)) is False

    def test_reads_a_naive_exam_date_without_raising(self):
        """`ExamPrep.examDate` is stored **without** an offset while the ORM declares it as having one, so
        asyncpg hands it back naive. Comparing that against an aware `now` raises `TypeError` — the exact
        defect that made `GET /progress/goals` a 500 for any goal with a target date."""
        naive = (NOW - timedelta(days=2)).replace(tzinfo=None)
        assert prep_outcome_service.is_awaiting_review(_prep(exam_date=naive)) is True


# ---------------------------------------------------------------------------
# Recording an answer
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    @pytest.mark.asyncio
    async def test_a_sat_exam_completes_the_preparation(self, repo):
        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "sat", "experienceRating": 4, "preparationRating": 5},
        )

        assert outcome.attended == "sat"
        assert outcome.preparation_rating == 5
        assert repo.prep.status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_a_missed_exam_also_concludes_it_but_records_that_it_was_missed(self, repo):
        """`COMPLETED` here means *this preparation is finished*, not *you passed*. The status is a
        lifecycle value; the outcome row carries what actually happened, which is the whole point of
        separating them."""
        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "missed"}
        )

        assert repo.prep.status == "COMPLETED"
        assert outcome.attended == "missed"
        assert outcome.experience_rating is None

    @pytest.mark.asyncio
    async def test_a_missed_exam_may_still_rate_the_preparation(self, repo):
        """The rating this whole exercise exists to collect. Someone who missed the exam can still have a
        view on whether the preparation was any good, and refusing it would discard the only signal on the
        row that says anything about us."""
        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "missed", "preparationRating": 2},
        )
        assert outcome.preparation_rating == 2

    @pytest.mark.asyncio
    async def test_rating_an_exam_nobody_sat_is_refused(self, repo):
        """Refused rather than silently dropped, which is the rule this backend applies everywhere —
        see `goal_service._reject_asserted_current_value`. A learner who rates something and watches the
        rating vanish has been overruled with no explanation."""
        with pytest.raises(ValidationError):
            await prep_outcome_service.record_outcome(
                user_id=OWNER,
                prep_id="prep-1",
                data={"attended": "missed", "experienceRating": 3},
            )

    @pytest.mark.asyncio
    async def test_a_postponed_exam_moves_to_the_date_the_learner_gave(self, repo):
        """**The one path on which an exam date moves**, and it moves because the learner supplied it
        rather than because anything inferred a new one."""
        new_date = NOW + timedelta(days=14)
        await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "postponed", "postponedTo": new_date},
        )

        assert repo.prep.exam_date == new_date
        assert repo.prep.status == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_postponing_starts_the_ask_budget_again(self, repo):
        """A new sitting is a new question. Leaving the budget spent would mean the learner is never asked
        about the exam they actually sat."""
        repo.prep.review_reminders_sent = 2
        await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "postponed", "postponedTo": NOW + timedelta(days=10)},
        )

        assert repo.prep.review_reminders_sent == 0
        assert repo.prep.review_asked_at is None
        assert repo.prep.review_declined_at is None

    @pytest.mark.asyncio
    async def test_a_postponed_exam_needs_its_new_date(self, repo):
        with pytest.raises(ValidationError):
            await prep_outcome_service.record_outcome(
                user_id=OWNER, prep_id="prep-1", data={"attended": "postponed"}
            )

    @pytest.mark.asyncio
    async def test_a_new_date_without_postponing_is_refused(self, repo):
        """The converse. A learner who says they sat the exam and also supplies a new date has sent two
        contradictory facts, and guessing which they meant would move an exam nobody postponed."""
        with pytest.raises(ValidationError):
            await prep_outcome_service.record_outcome(
                user_id=OWNER,
                prep_id="prep-1",
                data={"attended": "sat", "postponedTo": NOW + timedelta(days=5)},
            )

    @pytest.mark.asyncio
    async def test_the_answer_keeps_the_sitting_it_is_about(self, repo):
        """`examDate` on the outcome is the date the answer refers to, not the preparation's current one.
        A postponed preparation moves `ExamPrep.examDate`, so without this the first sitting's answer
        would appear to be about the second."""
        first_sitting = repo.prep.exam_date
        await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "postponed", "postponedTo": NOW + timedelta(days=20)},
        )

        assert ("prep-1", first_sitting) in repo.outcomes
        assert repo.prep.exam_date != first_sitting

    @pytest.mark.asyncio
    async def test_answering_twice_updates_rather_than_recording_two_exams(self, repo):
        """Idempotent per sitting, matching `upsert_readiness_snapshot`. A retried submit, or a learner
        correcting their rating, must not turn one exam into two."""
        await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat", "preparationRating": 2}
        )
        await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat", "preparationRating": 5}
        )

        assert len(repo.outcomes) == 1
        assert next(iter(repo.outcomes.values())).preparation_rating == 5

    @pytest.mark.asyncio
    async def test_another_learners_preparation_is_not_found(self, repo):
        with pytest.raises(NotFoundError):
            await prep_outcome_service.record_outcome(
                user_id=INTRUDER, prep_id="prep-1", data={"attended": "sat"}
            )


# ---------------------------------------------------------------------------
# The calibration snapshot
# ---------------------------------------------------------------------------


class TestReadinessSnapshot:
    @pytest.mark.asyncio
    async def test_readiness_is_copied_onto_the_answer(self, repo):
        """The point of the whole change. `progress_percent` is a prediction that has never been compared
        against an outcome, because no outcome existed; copying it here is what makes it falsifiable."""
        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat"}
        )

        assert outcome.readiness_percent == 70.0
        assert outcome.topics_strong == 7
        # The target as it stood, so "did they reach what they were aiming at" survives them editing it.
        assert outcome.target_readiness == 85

    @pytest.mark.asyncio
    async def test_nothing_to_measure_is_null_rather_than_zero(self, monkeypatch):
        """A preparation with no topics has no readiness. `0` would claim a measured absence of it, which
        would then be scored against the outcome as if we had predicted certain failure.

        **Patched on the `prep_readiness` module, not on `prep_outcome_service`.** `_readiness_at_answer`
        does a function-local `from . import prep_readiness`, so setting the attribute on the *calling*
        module is invisible to it — the local import rebinds the real thing. The first version of this test
        did that, and passed for the wrong reason: the real call failed inside the function's own
        `try/except` and returned `{}`, which looks identical to the behaviour being asserted. Mutating the
        `topics_total <= 0` guard away left it green, which is how the hole was found.
        """
        from src.domains.personal_learning.services import prep_readiness

        fake = FakeRepo(_prep())
        monkeypatch.setattr(prep_outcome_service, "repo", fake)

        async def _noop(**kwargs):
            return None

        monkeypatch.setattr(prep_outcome_service, "_resolve_linked_goal", _noop)
        monkeypatch.setattr(prep_outcome_service, "_record_activity", _noop)

        called: list[list[str]] = []

        async def _no_topics(prep_ids):
            called.append(prep_ids)
            return {prep_ids[0]: _progress(topics_total=0)}

        monkeypatch.setattr(prep_readiness, "load_for_preparations", _no_topics)

        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat"}
        )

        # The stand-in really was reached. Without this the assertions below hold just as well when
        # readiness could not be loaded at all, which is a different situation with the same shape.
        assert called == [["prep-1"]]
        assert getattr(outcome, "readiness_percent", None) is None
        assert getattr(outcome, "topics_total", None) is None

    @pytest.mark.asyncio
    async def test_a_measured_preparation_snapshots_what_it_measured(self, monkeypatch):
        """The converse of the case above, through the same real code path rather than a stubbed
        `_readiness_at_answer` — so the two together pin the guard rather than the fixture."""
        from src.domains.personal_learning.services import prep_readiness

        fake = FakeRepo(_prep())
        monkeypatch.setattr(prep_outcome_service, "repo", fake)

        async def _noop(**kwargs):
            return None

        monkeypatch.setattr(prep_outcome_service, "_resolve_linked_goal", _noop)
        monkeypatch.setattr(prep_outcome_service, "_record_activity", _noop)

        async def _measured(prep_ids):
            return {prep_ids[0]: _progress(progress_percent=64.0, topics_total=11, topics_strong=7)}

        monkeypatch.setattr(prep_readiness, "load_for_preparations", _measured)

        outcome = await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat"}
        )

        assert outcome.readiness_percent == 64.0
        assert outcome.topics_total == 11
        assert outcome.topics_strong == 7


# ---------------------------------------------------------------------------
# Declining, results, and state
# ---------------------------------------------------------------------------


class TestDeclineAndResult:
    @pytest.mark.asyncio
    async def test_declining_does_not_complete_the_preparation(self, repo):
        """Nothing has been said about how it went, so asserting completion here would put back exactly
        the lie this change removes."""
        await prep_outcome_service.decline_review(user_id=OWNER, prep_id="prep-1")

        assert repo.prep.review_declined_at is not None
        assert repo.prep.status != "COMPLETED"

    @pytest.mark.asyncio
    async def test_a_result_needs_a_reviewed_sitting_first(self, repo):
        """A result without an answer is a score attached to a sitting nobody has confirmed happened."""
        with pytest.raises(ValidationError):
            await prep_outcome_service.record_result(
                user_id=OWNER, prep_id="prep-1", data={"resultValue": 72.0}
            )

    @pytest.mark.asyncio
    async def test_a_result_attaches_to_the_latest_sitting(self, repo):
        await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat"}
        )
        outcome = await prep_outcome_service.record_result(
            user_id=OWNER, prep_id="prep-1", data={"resultValue": 72.0, "resultScale": "100"}
        )

        assert outcome.result_value == 72.0
        assert outcome.result_scale == "100"
        assert outcome.result_recorded_at is not None
        # Still one sitting: recording a result is not a second exam.
        assert len(repo.outcomes) == 1


class TestReviewState:
    @pytest.mark.asyncio
    async def test_reports_awaiting_before_an_answer(self, repo):
        state = await prep_outcome_service.get_review_state(user_id=OWNER, prep_id="prep-1")

        assert state["awaiting"] is True
        assert state["outcome"] is None
        assert state["remindersSent"] == 0

    @pytest.mark.asyncio
    async def test_stops_awaiting_once_answered(self, repo):
        """Published as one field so a client does not infer it from `status` plus a date comparison —
        two clients inferring that separately is how they come to disagree about the same preparation."""
        await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat", "preparationRating": 4}
        )

        state = await prep_outcome_service.get_review_state(user_id=OWNER, prep_id="prep-1")

        assert state["awaiting"] is False
        assert state["outcome"] is not None
        assert state["outcome"].preparation_rating == 4

    @pytest.mark.asyncio
    async def test_an_answer_stops_the_waiting_even_if_the_status_write_was_lost(self, repo):
        """The reason `awaiting` reads the outcome as well as the status.

        The answer and the status change are two writes and not one transaction: `upsert_prep_outcome`
        commits, then `update_exam_prep` runs. If the second is lost, the learner has answered and the
        preparation still says `AWAITING_REVIEW` — and asking them again for an answer they already gave is
        the worst version of this feature. Checking both means the recorded answer wins.
        """
        await prep_outcome_service.record_outcome(
            user_id=OWNER, prep_id="prep-1", data={"attended": "sat"}
        )
        # Simulate the status write having been lost while the answer survived.
        repo.prep.status = "AWAITING_REVIEW"

        state = await prep_outcome_service.get_review_state(user_id=OWNER, prep_id="prep-1")

        assert state["awaiting"] is False
        assert state["outcome"] is not None

    @pytest.mark.asyncio
    async def test_a_postponed_preparation_is_not_awaiting_its_next_exam_yet(self, repo):
        """After postponing, the new date is in the future — there is nothing to review until it passes,
        and the earlier answer must not make the new sitting look answered."""
        await prep_outcome_service.record_outcome(
            user_id=OWNER,
            prep_id="prep-1",
            data={"attended": "postponed", "postponedTo": NOW + timedelta(days=30)},
        )

        state = await prep_outcome_service.get_review_state(user_id=OWNER, prep_id="prep-1")

        assert state["awaiting"] is False
        assert state["outcome"] is None
        # The first sitting is still real history, reachable through the history read.
        assert len(await prep_outcome_service.list_outcomes(user_id=OWNER, prep_id="prep-1")) == 1
