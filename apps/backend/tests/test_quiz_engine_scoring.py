"""Tests for quiz answering and scoring integrity (no DB required).

These guard the two defects Phase 4 fixed, each of which independently defeated
the point of withholding answer keys:

1. ``submit_answer`` looked the question up by id alone, so a question from any
   other session — including another learner's — could be answered and its answer
   key read back out of the response.
2. ``correctCount`` was incremented per submission, so resubmitting a question
   after being shown its answer pushed the score past 100%.

The repository is replaced with a fake that models session scoping the way the
database does, so a regression in the service is visible here rather than only in
an integration environment.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domains.personal_learning.services import quiz_engine
from src.shared.exceptions import MaigieError, NotFoundError

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)

OWNER = "user-owner"
INTRUDER = "user-intruder"


# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class FakeRepo:
    """In-memory stand-in that enforces the same scoping the database does.

    Sessions are keyed by ``(quiz_id, user_id)``. Questions are owned by a
    *preparation* and linked to the sessions that asked them, mirroring
    ``PrepQuestion`` and ``QuizSessionQuestion``, so a lookup that forgets to
    scope through the link cannot accidentally pass.
    """

    def __init__(self):
        self.sessions: dict[str, SimpleNamespace] = {}
        self.questions: dict[str, SimpleNamespace] = {}
        # (quizSessionId, prepQuestionId) pairs — the session-to-bank link.
        self.links: set[tuple[str, str]] = set()
        self.answers: list[SimpleNamespace] = []
        self.session_updates: list[tuple[str, dict]] = []
        self.attempts: list[tuple[str, bool]] = []
        self.observations: list[dict] = []
        # (quizSessionId, prepQuestionId) -> link
        self.links_by_key: dict[tuple[str, str], SimpleNamespace] = {}

    # --- seeding -------------------------------------------------------

    def add_session(self, quiz_id: str, user_id: str, *, status="IN_PROGRESS", total=2):
        self.sessions[quiz_id] = SimpleNamespace(
            id=quiz_id,
            user_id=user_id,
            prep_id="prep-1",
            mode="FULL_PRACTICE",
            topic_id=None,
            status=status,
            total_questions=total,
            correct_count=0,
            score_percentage=None,
            duration_seconds=None,
            completed_at=None,
            created_at=NOW,
        )
        return self.sessions[quiz_id]

    def add_question(self, question_id: str, quiz_id: str, *, key="right", topic_id="topic-1"):
        """Bank a question and link it to the session that asked it."""
        self.questions[question_id] = SimpleNamespace(
            id=question_id,
            prep_id="prep-1",
            question_text=f"Question {question_id}?",
            question_type="MULTIPLE_CHOICE",
            options=[key, "wrong a", "wrong b"],
            prep_topic_id=topic_id,
            correct_answer=key,
            explanation=f"why {key} is right",
            times_answered=0,
            times_correct=0,
        )
        self.links.add((quiz_id, question_id))
        self.links_by_key[(quiz_id, question_id)] = SimpleNamespace(
            quiz_session_id=quiz_id,
            prep_question_id=question_id,
            order_index=0,
            hint_count=0,
        )
        return self.questions[question_id]

    def bank_question_without_asking(self, question_id: str, *, key="right"):
        """Bank a question that no session has asked.

        A learner may legitimately see such a question in the bank, which is why
        it must still not be answerable inside an unrelated session.
        """
        question = self.add_question(question_id, "__unlinked__", key=key)
        self.links.discard(("__unlinked__", question_id))
        return question

    # --- repository surface --------------------------------------------

    async def get_quiz_session(self, quiz_id: str, user_id: str):
        session = self.sessions.get(quiz_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    async def find_quiz_question(self, question_id: str, quiz_id: str):
        # The scoping that matters: a banked question resolves only through a
        # session that actually asked it.
        if (quiz_id, question_id) not in self.links:
            return None
        return self.questions.get(question_id)

    async def record_question_attempt(self, question_id: str, *, correct: bool):
        self.attempts.append((question_id, correct))
        question = self.questions.get(question_id)
        if question is not None:
            question.times_answered += 1
            question.times_correct += 1 if correct else 0

    async def find_session_question_link(self, *, quiz_session_id: str, prep_question_id: str):
        return self.links_by_key.get((quiz_session_id, prep_question_id))

    async def increment_session_question_hints(
        self, *, quiz_session_id: str, prep_question_id: str
    ):
        link = self.links_by_key[(quiz_session_id, prep_question_id)]
        link.hint_count += 1
        return link.hint_count

    async def record_practice_observation(self, data: dict):
        self.observations.append(dict(data))
        return SimpleNamespace(**data)

    async def find_quiz_answer(self, quiz_id: str, question_id: str):
        for answer in self.answers:
            if answer.quiz_session_id == quiz_id and answer.question_id == question_id:
                return answer
        return None

    async def create_quiz_answer(self, data: dict):
        answer = SimpleNamespace(
            quiz_session_id=data["quizSessionId"],
            question_id=data["questionId"],
            user_answer=data["userAnswer"],
            is_correct=data["isCorrect"],
            time_taken_seconds=data.get("timeTakenSeconds"),
            created_at=NOW,
        )
        self.answers.append(answer)
        return answer

    async def count_correct_quiz_answers(self, quiz_id: str):
        return len(
            {a.question_id for a in self.answers if a.quiz_session_id == quiz_id and a.is_correct}
        )

    async def sync_quiz_correct_count(self, quiz_id: str):
        """One statement in SQL; here, the same derivation applied in place.

        Recorded in `session_updates` like any other write, so the existing
        assertions about `correctCount` still see it.
        """
        await self.update_quiz_session(
            quiz_id, {"correctCount": await self.count_correct_quiz_answers(quiz_id)}
        )

    async def list_quiz_answers(self, quiz_id: str):
        return [a for a in self.answers if a.quiz_session_id == quiz_id]

    async def update_quiz_session(self, quiz_id: str, data: dict):
        self.session_updates.append((quiz_id, data))
        session = self.sessions.get(quiz_id)
        if session is not None:
            for key, value in data.items():
                attr = {
                    "correctCount": "correct_count",
                    "status": "status",
                    "scorePercentage": "score_percentage",
                    "durationSeconds": "duration_seconds",
                    "completedAt": "completed_at",
                    "totalQuestions": "total_questions",
                }.get(key)
                if attr:
                    setattr(session, attr, value)
        return session

    # --- assertion helpers --------------------------------------------

    def last_correct_count_written(self):
        for _, data in reversed(self.session_updates):
            if "correctCount" in data:
                return data["correctCount"]
        return None


@pytest.fixture
def repo(monkeypatch):
    """Swap the module-level repository and neutralise DB-touching side effects."""
    fake = FakeRepo()
    monkeypatch.setattr(quiz_engine, "repo", fake)
    # Mastery recalculation is fire-and-forget and hits the database directly.
    monkeypatch.setattr(quiz_engine, "_update_topic_mastery_safe", AsyncMock())
    return fake


# ---------------------------------------------------------------------------
# TestAnswerOwnership
# ---------------------------------------------------------------------------


class TestAnswerOwnership:
    """A question is answerable only inside the session it belongs to."""

    async def test_owner_can_answer_their_own_question(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        result = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert result["isCorrect"] is True
        assert result["correctAnswer"] == "right"

    async def test_another_users_session_is_not_found(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=INTRUDER,
                quiz_id="quiz-1",
                data={"question_id": "q1", "user_answer": "right"},
            )
        assert repo.answers == []

    async def test_question_from_another_session_is_not_answerable(self, repo):
        """The regression guard for the answer-key leak.

        The intruder owns ``quiz-2``, so the session check passes. Before the fix
        the question was then loaded by id alone, so ``q1`` resolved and the
        response handed back its ``correctAnswer`` and ``explanation``.
        """
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="the secret answer")
        repo.add_session("quiz-2", INTRUDER)

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=INTRUDER,
                quiz_id="quiz-2",
                data={"question_id": "q1", "user_answer": "guess"},
            )

        # Nothing recorded against either session.
        assert repo.answers == []

    async def test_question_from_own_other_session_is_also_rejected(self, repo):
        """Scoping is per session, not merely per user: it must not be possible to
        pull a question forward from an earlier session of your own."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")
        repo.add_session("quiz-2", OWNER)

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=OWNER,
                quiz_id="quiz-2",
                data={"question_id": "q1", "user_answer": "right"},
            )

    async def test_unknown_question_is_not_found(self, repo):
        repo.add_session("quiz-1", OWNER)

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=OWNER,
                quiz_id="quiz-1",
                data={"question_id": "nope", "user_answer": "x"},
            )


# ---------------------------------------------------------------------------
# TestScoreIntegrity
# ---------------------------------------------------------------------------


class TestScoreIntegrity:
    """The score is recomputed from persisted answers, never accumulated."""

    async def test_correct_answer_writes_a_recomputed_count(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert repo.last_correct_count_written() == 1

    async def test_wrong_answer_does_not_raise_the_count(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "wrong a"}
        )

        assert repo.last_correct_count_written() == 0

    async def test_resubmitting_replays_and_does_not_rescore(self, repo):
        """The regression guard for score inflation.

        The first submission tells the learner the answer. Before the fix, sending
        it back counted again, so a wrong answer could be converted into a right
        one and the score could exceed 100%.
        """
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        first = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "wrong a"}
        )
        assert first["isCorrect"] is False
        assert first["alreadyAnswered"] is False
        # The learner now knows the answer.
        assert first["correctAnswer"] == "right"

        second = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert second["alreadyAnswered"] is True
        # The stored result stands; the resubmission did not become correct.
        assert second["isCorrect"] is False
        assert len(repo.answers) == 1
        assert repo.last_correct_count_written() == 0

    async def test_repeated_correct_submissions_do_not_accumulate(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        for _ in range(5):
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert len(repo.answers) == 1
        assert repo.last_correct_count_written() == 1

    async def test_count_cannot_exceed_question_count(self, repo):
        """Two questions, answered repeatedly, can never score more than two."""
        repo.add_session("quiz-1", OWNER, total=2)
        repo.add_question("q1", "quiz-1", key="right")
        repo.add_question("q2", "quiz-1", key="right")

        for question_id in ("q1", "q2", "q1", "q2"):
            await quiz_engine.submit_answer(
                user_id=OWNER,
                quiz_id="quiz-1",
                data={"question_id": question_id, "user_answer": "right"},
            )

        assert repo.last_correct_count_written() == 2

    async def test_replay_is_safe_for_a_client_retry(self, repo):
        """A double-clicked button or a network retry must not error."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        payload = {"question_id": "q1", "user_answer": "right"}
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)
        replay = await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)

        assert replay["alreadyAnswered"] is True
        assert replay["explanation"] == "why right is right"

    async def test_mastery_update_is_triggered_for_attributed_questions(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", topic_id="topic-7")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        # The learner is passed too, because the competence model that replaced the
        # lifetime average reads that learner's observations.
        quiz_engine._update_topic_mastery_safe.assert_called_once_with("topic-7", user_id=OWNER)

    async def test_unattributed_question_skips_mastery_update(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", topic_id=None)

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        quiz_engine._update_topic_mastery_safe.assert_not_called()


# ---------------------------------------------------------------------------
# TestSessionStatusGuards
# ---------------------------------------------------------------------------


class TestSessionStatusGuards:
    """Answering is only meaningful while a session is playable."""

    async def test_completed_session_rejects_answers(self, repo):
        repo.add_session("quiz-1", OWNER, status="COMPLETED")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert exc.value.code == "QUIZ_ALREADY_COMPLETED"
        assert exc.value.status_code == 409

    async def test_generating_session_rejects_answers(self, repo):
        repo.add_session("quiz-1", OWNER, status="GENERATING")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert exc.value.code == "QUIZ_GENERATING"

    async def test_failed_session_rejects_answers(self, repo):
        repo.add_session("quiz-1", OWNER, status="FAILED")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert exc.value.code == "QUIZ_GENERATION_FAILED"

    async def test_missing_fields_are_a_400_not_a_500(self, repo):
        repo.add_session("quiz-1", OWNER)

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1"}
            )

        assert exc.value.status_code == 400
        assert exc.value.code == "QUIZ_ANSWER_INVALID"


# ---------------------------------------------------------------------------
# TestCompleteQuiz
# ---------------------------------------------------------------------------


@pytest.fixture
def completion(monkeypatch, repo):
    """Neutralise completion side effects that reach other services or the DB."""
    monkeypatch.setattr(quiz_engine, "_compute_topic_breakdown", AsyncMock(return_value=[]))

    from src.domains.personal_learning.services import activity_feed_service, milestone_service

    monkeypatch.setattr(activity_feed_service, "record", AsyncMock())
    monkeypatch.setattr(milestone_service, "check_milestones", AsyncMock())
    return SimpleNamespace(
        repo=repo,
        activity=activity_feed_service.record,
        milestones=milestone_service.check_milestones,
    )


class TestCompleteQuiz:
    async def test_score_is_derived_from_persisted_answers(self, completion):
        repo = completion.repo
        repo.add_session("quiz-1", OWNER, total=4)
        for qid in ("q1", "q2", "q3", "q4"):
            repo.add_question(qid, "quiz-1", key="right")
        for qid in ("q1", "q2", "q3"):
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": qid, "user_answer": "right"}
            )

        summary = await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert summary["correctCount"] == 3
        assert summary["totalQuestions"] == 4
        assert summary["scorePercentage"] == 75.0

    async def test_question_less_session_scores_zero_without_inventing_a_denominator(
        self, completion
    ):
        """``total_questions or 1`` used to report a plausible-looking 0% for a
        session that never asked anything."""
        completion.repo.add_session("quiz-1", OWNER, total=0)

        summary = await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert summary["totalQuestions"] == 0
        assert summary["scorePercentage"] == 0.0

    async def test_completing_twice_does_not_re_record_activity(self, completion):
        repo = completion.repo
        repo.add_session("quiz-1", OWNER, total=1)
        repo.add_question("q1", "quiz-1", key="right")
        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        first = await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")
        second = await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert first["scorePercentage"] == second["scorePercentage"]
        completion.activity.assert_called_once()
        completion.milestones.assert_called_once()

    async def test_another_users_session_cannot_be_completed(self, completion):
        completion.repo.add_session("quiz-1", OWNER)

        with pytest.raises(NotFoundError):
            await quiz_engine.complete_quiz(user_id=INTRUDER, quiz_id="quiz-1")

    async def test_failed_session_cannot_be_completed(self, completion):
        completion.repo.add_session("quiz-1", OWNER, status="FAILED")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert exc.value.code == "QUIZ_GENERATION_FAILED"

    async def test_generating_session_cannot_be_completed(self, completion):
        completion.repo.add_session("quiz-1", OWNER, status="GENERATING")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert exc.value.code == "QUIZ_GENERATING"

    async def test_completion_marks_the_session_and_writes_the_score(self, completion):
        repo = completion.repo
        repo.add_session("quiz-1", OWNER, total=2)
        repo.add_question("q1", "quiz-1", key="right")
        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        session = repo.sessions["quiz-1"]
        assert session.status == "COMPLETED"
        assert session.correct_count == 1
        assert session.score_percentage == 50.0
        assert session.completed_at is not None

    async def test_duration_is_computed_when_the_client_sends_none(self, completion):
        completion.repo.add_session("quiz-1", OWNER, total=1)

        await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1")

        assert completion.repo.sessions["quiz-1"].duration_seconds is not None

    async def test_client_supplied_duration_is_respected(self, completion):
        completion.repo.add_session("quiz-1", OWNER, total=1)

        await quiz_engine.complete_quiz(user_id=OWNER, quiz_id="quiz-1", duration_seconds=42)

        assert completion.repo.sessions["quiz-1"].duration_seconds == 42


# ---------------------------------------------------------------------------
# TestQuestionBankScoping
# ---------------------------------------------------------------------------


class TestQuestionBankScoping:
    """A banked question outlives its session, which must not widen access.

    Promoting questions to the preparation means a question is now visible in a
    browsing surface. That makes it more important, not less, that answering is
    only possible through a session that actually asked it.
    """

    async def test_banked_but_unasked_question_is_not_answerable(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.bank_question_without_asking("bank-1", key="right")

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=OWNER,
                quiz_id="quiz-1",
                data={"question_id": "bank-1", "user_answer": "right"},
            )
        assert repo.answers == []

    async def test_question_from_an_earlier_session_of_the_same_prep_is_not_answerable(self, repo):
        """Both sessions belong to the same learner and the same preparation, so
        only the session link distinguishes them."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")
        repo.add_session("quiz-2", OWNER)

        with pytest.raises(NotFoundError):
            await quiz_engine.submit_answer(
                user_id=OWNER,
                quiz_id="quiz-2",
                data={"question_id": "q1", "user_answer": "right"},
            )

    async def test_reused_question_is_answerable_in_each_session_that_asked_it(self, repo):
        """The point of a bank: one question, many sessions, separate answers."""
        repo.add_session("quiz-1", OWNER)
        repo.add_session("quiz-2", OWNER)
        repo.add_question("shared", "quiz-1", key="right")
        repo.links.add(("quiz-2", "shared"))

        first = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "shared", "user_answer": "right"}
        )
        second = await quiz_engine.submit_answer(
            user_id=OWNER,
            quiz_id="quiz-2",
            data={"question_id": "shared", "user_answer": "wrong a"},
        )

        # Independent answers, not an idempotent replay: different sessions.
        assert first["alreadyAnswered"] is False
        assert second["alreadyAnswered"] is False
        assert first["isCorrect"] is True
        assert second["isCorrect"] is False
        assert len(repo.answers) == 2


# ---------------------------------------------------------------------------
# TestQuestionStatistics
# ---------------------------------------------------------------------------


class TestQuestionStatistics:
    """Lifetime per-question statistics, only expressible now questions persist."""

    async def test_attempt_is_recorded_once_per_answer(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert repo.attempts == [("q1", True)]

    async def test_incorrect_attempt_is_recorded_as_incorrect(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "wrong a"}
        )

        assert repo.attempts == [("q1", False)]

    async def test_replayed_submission_does_not_double_count_the_attempt(self, repo):
        """Statistics must not drift for the same reason the score must not."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        payload = {"question_id": "q1", "user_answer": "right"}
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)

        assert repo.attempts == [("q1", True)]
        assert repo.questions["q1"].times_answered == 1

    async def test_statistics_accumulate_across_sessions(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_session("quiz-2", OWNER)
        repo.add_question("shared", "quiz-1", key="right")
        repo.links.add(("quiz-2", "shared"))

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "shared", "user_answer": "right"}
        )
        await quiz_engine.submit_answer(
            user_id=OWNER,
            quiz_id="quiz-2",
            data={"question_id": "shared", "user_answer": "wrong a"},
        )

        assert repo.questions["shared"].times_answered == 2
        assert repo.questions["shared"].times_correct == 1

    async def test_a_rejected_answer_records_no_attempt(self, repo):
        repo.add_session("quiz-1", OWNER, status="COMPLETED")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError):
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert repo.attempts == []


# ---------------------------------------------------------------------------
# TestHintRequests
# ---------------------------------------------------------------------------


class TestHintRequests:
    """Hints are pulled by the learner, counted, and never a penalty."""

    async def test_level_one_returns_the_nudge(self, repo):
        repo.add_session("quiz-1", OWNER)
        question = repo.add_question("q1", "quiz-1", key="right")
        question.hint_nudge = "Think about the assumption."

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=1
        )

        assert result["nudge"] == "Think about the assumption."
        assert result["eliminatedOption"] is None
        assert result["hintAvailable"] is True

    async def test_a_question_with_no_nudge_still_gets_a_hint_at_level_one(self, repo):
        """Found when the hint button was first wired to the UI.

        A level-1 request on a question with no nudge returned
        `hintAvailable: false` **and still counted the request** — telling the
        learner nothing could be offered while level 2 would have offered
        something, and charging them a hint to find out. Every question generated
        before `hintNudge` existed is in this position: measured at 32 of 118.

        Eliminating an option is a stronger hint than a nudge, so it is not the
        first choice. It is simply better than nothing, and still not the answer.
        """
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        repo.questions["q1"].hint_nudge = None

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=1
        )

        assert result["hintAvailable"] is True
        assert result["eliminatedOption"] is not None
        assert result["eliminatedOption"] != "right"

    async def test_a_question_with_nothing_to_offer_says_so(self, repo):
        """`hintAvailable: false` has to remain reachable, or it means nothing.

        Two options and no nudge: eliminating one would leave no choice at all, so
        there is genuinely nothing to give.
        """
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        repo.questions["q1"].hint_nudge = None
        repo.questions["q1"].options = ["right", "wrong a"]

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=1
        )

        assert result["hintAvailable"] is False
        assert result["nudge"] is None
        assert result["eliminatedOption"] is None

    async def test_a_nudge_is_still_preferred_at_level_one(self, repo):
        """The fallback must not have replaced the nudge when one exists.

        Level 1 stays the weaker hint where it can: a nudge points at the concept,
        while eliminating an option narrows the choice for the learner.
        """
        repo.add_session("quiz-1", OWNER)
        question = repo.add_question("q1", "quiz-1", key="right")
        question.hint_nudge = "Think about the assumption."

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=1
        )

        assert result["nudge"] == "Think about the assumption."
        assert result["eliminatedOption"] is None

    async def test_level_two_also_eliminates_a_wrong_option(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=2
        )

        assert result["eliminatedOption"] in {"wrong a", "wrong b"}
        assert result["eliminatedOption"] != "right"

    async def test_each_request_is_counted(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")

        first = await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")
        second = await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")

        assert (first["hintCount"], second["hintCount"]) == (1, 2)

    async def test_level_is_clamped_rather_than_rejected(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")

        low = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=-5
        )
        high = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=99
        )

        assert low["level"] == quiz_engine.HINT_LEVEL_NUDGE
        assert high["level"] == quiz_engine.MAX_HINT_LEVEL

    async def test_reports_honestly_when_there_is_no_hint_to_give(self, repo):
        """Better than a hint-shaped object containing no hint."""
        repo.add_session("quiz-1", OWNER)
        question = repo.add_question("q1", "quiz-1")
        question.hint_nudge = None
        question.options = ["right", "wrong"]  # too few to eliminate from

        result = await quiz_engine.request_hint(
            user_id=OWNER, quiz_id="quiz-1", question_id="q1", level=2
        )

        assert result["hintAvailable"] is False
        assert result["nudge"] is None
        assert result["eliminatedOption"] is None

    async def test_a_hint_after_answering_is_refused(self, repo):
        """The key is already disclosed, and allowing it would let hint counts be
        run up after the fact, corrupting the signal rather than recording it."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")

        assert exc.value.code == "QUESTION_ALREADY_ANSWERED"

    async def test_hints_are_refused_on_a_completed_session(self, repo):
        repo.add_session("quiz-1", OWNER, status="COMPLETED")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")

        assert exc.value.code == "QUIZ_NOT_IN_PROGRESS"

    async def test_another_learners_session_is_not_found(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")

        with pytest.raises(NotFoundError):
            await quiz_engine.request_hint(user_id=INTRUDER, quiz_id="quiz-1", question_id="q1")

    async def test_a_question_from_another_session_is_not_hintable(self, repo):
        """Hints are scoped exactly as answering is — otherwise the hint endpoint
        becomes a way to read another session's question."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1")
        repo.add_session("quiz-2", INTRUDER)

        with pytest.raises(NotFoundError):
            await quiz_engine.request_hint(user_id=INTRUDER, quiz_id="quiz-2", question_id="q1")


# ---------------------------------------------------------------------------
# TestPracticeObservations
# ---------------------------------------------------------------------------


class TestPracticeObservations:
    """Phase A: keep the evidence, not just the verdict."""

    async def test_an_answer_produces_an_observation(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right", topic_id="topic-7")

        await quiz_engine.submit_answer(
            user_id=OWNER,
            quiz_id="quiz-1",
            data={"question_id": "q1", "user_answer": "right", "time_taken_seconds": 12},
        )

        assert len(repo.observations) == 1
        observation = repo.observations[0]
        assert observation["userId"] == OWNER
        assert observation["prepId"] == "prep-1"
        assert observation["prepTopicId"] == "topic-7"
        assert observation["prepQuestionId"] == "q1"
        assert observation["quizSessionId"] == "quiz-1"
        assert observation["isCorrect"] is True
        assert observation["responseMs"] == 12_000

    async def test_an_incorrect_answer_is_observed_too(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "wrong a"}
        )

        assert repo.observations[0]["isCorrect"] is False

    async def test_missing_timing_is_null_not_zero(self, repo):
        """Null must stay distinguishable from "answered instantly"."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert repo.observations[0]["responseMs"] is None

    async def test_hints_taken_are_carried_into_the_observation(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")
        await quiz_engine.request_hint(user_id=OWNER, quiz_id="quiz-1", question_id="q1")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        observation = repo.observations[0]
        assert observation["hintUsed"] is True
        assert observation["hintCount"] == 2

    async def test_no_hints_records_hint_used_false(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert repo.observations[0]["hintUsed"] is False
        assert repo.observations[0]["hintCount"] == 0

    async def test_difficulty_is_copied_at_answer_time(self, repo):
        """Copied, not joined: difficulty may be recalibrated later."""
        repo.add_session("quiz-1", OWNER)
        question = repo.add_question("q1", "quiz-1", key="right")
        question.difficulty = "HARD"

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert repo.observations[0]["difficulty"] == "HARD"

    async def test_a_replayed_answer_does_not_observe_twice(self, repo):
        """The same reason the score does not move: it is one attempt, not two."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        payload = {"question_id": "q1", "user_answer": "right"}
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="quiz-1", data=payload)

        assert len(repo.observations) == 1

    async def test_a_rejected_answer_produces_no_observation(self, repo):
        repo.add_session("quiz-1", OWNER, status="COMPLETED")
        repo.add_question("q1", "quiz-1")

        with pytest.raises(MaigieError):
            await quiz_engine.submit_answer(
                user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
            )

        assert repo.observations == []

    async def test_an_unattributed_question_still_produces_an_observation(self, repo):
        """Losing topic attribution should not lose the evidence entirely."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right", topic_id=None)

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert len(repo.observations) == 1
        assert repo.observations[0]["prepTopicId"] is None

    async def test_a_failed_observation_does_not_fail_the_answer(self, repo, monkeypatch):
        """The observation is valuable; the learner's answer is more valuable."""

        async def _boom(data):
            raise RuntimeError("observation store unavailable")

        monkeypatch.setattr(repo, "record_practice_observation", _boom)
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        result = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert result["isCorrect"] is True
        assert len(repo.answers) == 1


# ---------------------------------------------------------------------------
# TestExamConditions
# ---------------------------------------------------------------------------


class TestExamConditions:
    """`PAST_PAPER_SIM` defers all feedback to the end.

    A deliberate, narrow exception to the per-question disclosure boundary: a
    simulation that marks each question as you go simulates nothing. The guarantee
    that matters is unchanged — a learner still never sees the answer to a question
    they have not committed to.
    """

    async def test_answering_discloses_nothing(self, repo):
        repo.add_session("exam-1", OWNER)
        repo.sessions["exam-1"].mode = "PAST_PAPER_SIM"
        repo.add_question("q1", "exam-1", key="right")

        result = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="exam-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert result["feedbackDeferred"] is True
        assert result["isCorrect"] is None
        assert result["correctAnswer"] is None
        assert result["explanation"] is None

    async def test_the_answer_is_still_recorded_and_scored(self, repo):
        """Withholding feedback is not the same as not marking the paper."""
        repo.add_session("exam-1", OWNER)
        repo.sessions["exam-1"].mode = "PAST_PAPER_SIM"
        repo.add_question("q1", "exam-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="exam-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert len(repo.answers) == 1
        assert repo.answers[0].is_correct is True
        assert repo.last_correct_count_written() == 1

    async def test_a_replayed_answer_also_discloses_nothing(self, repo):
        repo.add_session("exam-1", OWNER)
        repo.sessions["exam-1"].mode = "PAST_PAPER_SIM"
        repo.add_question("q1", "exam-1", key="right")

        payload = {"question_id": "q1", "user_answer": "right"}
        await quiz_engine.submit_answer(user_id=OWNER, quiz_id="exam-1", data=payload)
        replay = await quiz_engine.submit_answer(user_id=OWNER, quiz_id="exam-1", data=payload)

        assert replay["alreadyAnswered"] is True
        assert replay["correctAnswer"] is None

    async def test_hints_are_refused(self, repo):
        repo.add_session("exam-1", OWNER)
        repo.sessions["exam-1"].mode = "PAST_PAPER_SIM"
        repo.add_question("q1", "exam-1")

        with pytest.raises(MaigieError) as exc:
            await quiz_engine.request_hint(user_id=OWNER, quiz_id="exam-1", question_id="q1")

        assert exc.value.code == "QUIZ_EXAM_CONDITIONS"

    async def test_observations_are_still_recorded(self, repo):
        """The learner gets no feedback; the system still learns from the attempt."""
        repo.add_session("exam-1", OWNER)
        repo.sessions["exam-1"].mode = "PAST_PAPER_SIM"
        repo.add_question("q1", "exam-1", key="right")

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="exam-1", data={"question_id": "q1", "user_answer": "wrong a"}
        )

        assert len(repo.observations) == 1
        assert repo.observations[0]["isCorrect"] is False

    async def test_normal_modes_are_unaffected(self, repo):
        """The exception must stay narrow."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")

        result = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert result["feedbackDeferred"] is False
        assert result["correctAnswer"] == "right"

    def test_the_mode_predicate_is_narrow(self):
        assert quiz_engine.defers_feedback("PAST_PAPER_SIM") is True
        assert quiz_engine.defers_feedback("past_paper_sim") is True
        for mode in ("FULL_PRACTICE", "WEAK_AREAS", "TOPIC_FOCUS", "ADAPTIVE", "QUICK_REVIEW"):
            assert quiz_engine.defers_feedback(mode) is False
        assert quiz_engine.defers_feedback(None) is False


# ---------------------------------------------------------------------------
# TestAnswerLatency
# ---------------------------------------------------------------------------


class TestAnswerLatency:
    """Submitting an answer must not be a long chain of sequential round trips.

    Reported from real use: "check answer takes long". Nine repository calls ran
    one after another, and against a hosted database each one costs a real round
    trip, so the learner waited for all nine before learning whether they were
    right.

    These tests pin the *shape* of the work rather than a wall-clock number, which
    would be flaky. Depth is what matters: a call issued concurrently with others
    costs nothing extra, a call awaited on its own costs a round trip.
    """

    def _instrument(self, repo):
        """Record the order and concurrency of repository calls."""
        import asyncio

        timeline: list[tuple[str, int]] = []
        wave = {"n": 0}

        def wrap(name):
            original = getattr(repo, name)

            async def traced(*args, **kwargs):
                timeline.append((name, wave["n"]))
                # Yield, so anything gathered alongside this call is recorded in
                # the same wave rather than the next one.
                await asyncio.sleep(0)
                return await original(*args, **kwargs)

            return traced

        for name in (
            "get_quiz_session",
            "find_quiz_question",
            "find_quiz_answer",
            "find_session_question_link",
            "create_quiz_answer",
            "sync_quiz_correct_count",
            "record_question_attempt",
            "record_practice_observation",
        ):
            if hasattr(repo, name):
                setattr(repo, name, wrap(name))
        return timeline

    async def test_the_initial_reads_are_issued_together(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        timeline = self._instrument(repo)

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        names = [name for name, _ in timeline]
        reads = {
            "get_quiz_session",
            "find_quiz_question",
            "find_quiz_answer",
            "find_session_question_link",
        }
        # All four appear before the write, meaning none of them waited on another.
        write_index = names.index("create_quiz_answer")
        assert reads.issubset(set(names[:write_index]))

    async def test_the_bookkeeping_writes_are_issued_together(self, repo):
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        timeline = self._instrument(repo)

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        names = [name for name, _ in timeline]
        answer_index = names.index("create_quiz_answer")
        after = names[answer_index + 1 :]
        # The three bookkeeping writes all follow the answer row and none of them
        # waits on another, so they cost one round trip between them rather than
        # three. The observation is a single statement now that its hint count is
        # read in the batch above.
        assert set(after) == {
            "sync_quiz_correct_count",
            "record_question_attempt",
            "record_practice_observation",
        }

    async def test_an_answer_costs_a_bounded_number_of_calls(self, repo):
        """A guard against a new sequential call being added without noticing."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        timeline = self._instrument(repo)

        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        # Four reads, one answer insert, three bookkeeping writes. Raising this
        # number is a decision to make the learner wait longer, so it should be a
        # deliberate one.
        assert len(timeline) == 8

    async def test_a_replayed_answer_short_circuits(self, repo):
        """A resubmission does no writes at all, so a double-tap is cheap."""
        repo.add_session("quiz-1", OWNER)
        repo.add_question("q1", "quiz-1", key="right")
        await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        timeline = self._instrument(repo)
        result = await quiz_engine.submit_answer(
            user_id=OWNER, quiz_id="quiz-1", data={"question_id": "q1", "user_answer": "right"}
        )

        assert result["alreadyAnswered"] is True
        assert not [name for name, _ in timeline if name.startswith(("create", "record", "sync"))]
