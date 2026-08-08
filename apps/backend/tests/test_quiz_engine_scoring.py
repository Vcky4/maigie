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

    Sessions are keyed by ``(quiz_id, user_id)`` and questions carry the session
    they belong to, so a lookup that forgets to scope cannot accidentally pass.
    """

    def __init__(self):
        self.sessions: dict[str, SimpleNamespace] = {}
        self.questions: dict[str, SimpleNamespace] = {}
        self.answers: list[SimpleNamespace] = []
        self.session_updates: list[tuple[str, dict]] = []

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
        self.questions[question_id] = SimpleNamespace(
            id=question_id,
            quiz_session_id=quiz_id,
            question_text=f"Question {question_id}?",
            question_type="MULTIPLE_CHOICE",
            options=[key, "wrong a", "wrong b"],
            order_index=0,
            prep_topic_id=topic_id,
            correct_answer=key,
            explanation=f"why {key} is right",
        )
        return self.questions[question_id]

    # --- repository surface --------------------------------------------

    async def get_quiz_session(self, quiz_id: str, user_id: str):
        session = self.sessions.get(quiz_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    async def find_quiz_question(self, question_id: str, quiz_id: str):
        question = self.questions.get(question_id)
        # The scoping that matters: a question only resolves inside its own session.
        if question is None or question.quiz_session_id != quiz_id:
            return None
        return question

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

        quiz_engine._update_topic_mastery_safe.assert_called_once_with("topic-7")

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
