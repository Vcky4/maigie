"""Tests for the preparation question bank listing (no DB required).

The bank is a browsing surface. Its most important property is a negative one:
opening it must not be a way to read answers without practising, which would
reopen the leak that withholding the key at quiz start closed.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import exam_prep_service
from src.shared.exceptions import NotFoundError

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)

OWNER = "user-owner"
INTRUDER = "user-intruder"

SECRET_KEY = "THE_ANSWER_CANARY"
SECRET_EXPLANATION = "THE_EXPLANATION_CANARY"
SECRET_TIP = "THE_EXAM_TIP_CANARY"


class FakeRepo:
    def __init__(self):
        self.preps: dict[tuple[str, str], SimpleNamespace] = {}
        self.questions: list[SimpleNamespace] = []
        self.searches: list[dict] = []
        # (userId, questionId) -> flag
        self.flags: dict[tuple[str, str], SimpleNamespace] = {}

    def add_prep(self, prep_id: str, user_id: str):
        self.preps[(prep_id, user_id)] = SimpleNamespace(id=prep_id, user_id=user_id)

    def add_question(
        self,
        question_id: str,
        prep_id: str,
        *,
        topic_id="topic-1",
        answered=0,
        correct=0,
        difficulty="MEDIUM",
        source="AI_GENERATED",
        source_year=None,
    ):
        self.questions.append(
            SimpleNamespace(
                id=question_id,
                prep_id=prep_id,
                prep_topic_id=topic_id,
                question_text=f"Question {question_id}?",
                question_type="MULTIPLE_CHOICE",
                options=[SECRET_KEY, "wrong a", "wrong b"],
                correct_answer=SECRET_KEY,
                explanation=SECRET_EXPLANATION,
                difficulty=difficulty,
                source=source,
                source_year=source_year,
                exam_tip=SECRET_TIP,
                times_answered=answered,
                times_correct=correct,
                created_at=NOW,
            )
        )

    async def find_exam_prep(self, prep_id: str, user_id: str):
        return self.preps.get((prep_id, user_id))

    async def search_prep_questions(
        self,
        prep_id,
        *,
        user_id,
        topic_id=None,
        difficulty=None,
        source=None,
        flagged_only=False,
        skip=0,
        take=20,
    ):
        self.searches.append(
            {
                "prep_id": prep_id,
                "user_id": user_id,
                "topic_id": topic_id,
                "difficulty": difficulty,
                "source": source,
                "flagged_only": flagged_only,
                "skip": skip,
                "take": take,
            }
        )
        matching = [q for q in self.questions if q.prep_id == prep_id]
        if topic_id:
            matching = [q for q in matching if q.prep_topic_id == topic_id]
        if difficulty:
            matching = [q for q in matching if q.difficulty == difficulty]
        if source:
            matching = [q for q in matching if q.source == source]

        # The flag join is scoped to the requesting learner.
        rows = [(q, self.flags.get((user_id, q.id))) for q in matching]
        if flagged_only:
            rows = [(q, f) for q, f in rows if f is not None]
        return rows[skip : skip + take], len(rows)

    async def find_prep_question(self, question_id: str, prep_id: str):
        for question in self.questions:
            if question.id == question_id and question.prep_id == prep_id:
                return question
        return None

    async def upsert_question_flag(self, *, user_id: str, prep_question_id: str, note=None):
        key = (user_id, prep_question_id)
        existing = self.flags.get(key)
        if existing is not None:
            if note is not None:
                existing.note = note
            return existing
        flag = SimpleNamespace(
            user_id=user_id, prep_question_id=prep_question_id, note=note, created_at=NOW
        )
        self.flags[key] = flag
        return flag

    async def delete_question_flag(self, *, user_id: str, prep_question_id: str):
        return self.flags.pop((user_id, prep_question_id), None) is not None


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(exam_prep_service, "repo", fake)
    return fake


class TestQuestionBankDisclosure:
    async def test_answer_key_is_never_returned(self, repo):
        """The whole point. Browsing is not a route to the answers."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert "correctAnswer" not in items[0]
        assert "explanation" not in items[0]
        assert SECRET_EXPLANATION not in str(items)

    async def test_question_body_is_returned(self, repo):
        """Browsing has to be useful, not merely safe."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["questionText"] == "Question q1?"
        assert items[0]["options"] == [SECRET_KEY, "wrong a", "wrong b"]

    async def test_options_still_contain_the_answer_text_and_that_is_fine(self, repo):
        """Documenting the boundary: the answer's text is one of the options, so it
        is necessarily present. What is absent is any marker of which one is right."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert SECRET_KEY in items[0]["options"]
        assert items[0].get("correctAnswer") is None


class TestQuestionBankOwnership:
    async def test_unknown_preparation_is_not_found(self, repo):
        with pytest.raises(NotFoundError):
            await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="nope")

    async def test_another_users_preparation_is_not_found(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        with pytest.raises(NotFoundError):
            await exam_prep_service.search_question_bank(user_id=INTRUDER, prep_id="prep-1")

    async def test_ownership_is_checked_before_any_question_is_read(self, repo):
        repo.add_prep("prep-1", OWNER)

        with pytest.raises(NotFoundError):
            await exam_prep_service.search_question_bank(user_id=INTRUDER, prep_id="prep-1")

        assert repo.searches == []


class TestQuestionBankStatistics:
    async def test_accuracy_is_none_until_attempted(self, repo):
        """Not measured, rather than always wrong."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1", answered=0, correct=0)

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["accuracyPercent"] is None
        assert items[0]["timesAnswered"] == 0

    async def test_accuracy_zero_is_distinct_from_unmeasured(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1", answered=3, correct=0)

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["accuracyPercent"] == 0.0

    async def test_accuracy_ratio(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1", answered=4, correct=3)

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["accuracyPercent"] == 75.0


class TestQuestionBankFiltering:
    async def test_scoped_to_the_preparation(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_prep("prep-2", OWNER)
        repo.add_question("q1", "prep-1")
        repo.add_question("q2", "prep-2")

        items, total = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert total == 1
        assert [i["id"] for i in items] == ["q1"]

    async def test_topic_filter_is_passed_through(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1", topic_id="topic-a")
        repo.add_question("q2", "prep-1", topic_id="topic-b")

        items, total = await exam_prep_service.search_question_bank(
            user_id=OWNER, prep_id="prep-1", topic_id="topic-b"
        )

        assert total == 1
        assert items[0]["id"] == "q2"

    async def test_pagination_offsets_are_derived_from_the_page(self, repo):
        repo.add_prep("prep-1", OWNER)
        for index in range(10):
            repo.add_question(f"q{index}", "prep-1")

        items, total = await exam_prep_service.search_question_bank(
            user_id=OWNER, prep_id="prep-1", page=3, page_size=4
        )

        assert repo.searches[-1]["skip"] == 8
        assert repo.searches[-1]["take"] == 4
        assert total == 10
        assert len(items) == 2

    async def test_empty_bank_returns_empty_rather_than_failing(self, repo):
        repo.add_prep("prep-1", OWNER)

        items, total = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items == []
        assert total == 0


class TestQuestionMetadata:
    """Difficulty and provenance are browsable; the exam tip is not."""

    async def test_metadata_is_returned(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1", difficulty="HARD", source="PAST_PAPER", source_year=2019)

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["difficulty"] == "HARD"
        assert items[0]["source"] == "PAST_PAPER"
        assert items[0]["sourceYear"] == 2019

    async def test_exam_tip_is_withheld_from_the_bank(self, repo):
        """A tip about a specific question can hint at its answer, so it sits on
        the answer key's side of the boundary rather than being neutral metadata."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert "examTip" not in items[0]
        assert SECRET_TIP not in str(items)

    async def test_difficulty_filter_is_passed_through(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("easy", "prep-1", difficulty="EASY")
        repo.add_question("hard", "prep-1", difficulty="HARD")

        items, total = await exam_prep_service.search_question_bank(
            user_id=OWNER, prep_id="prep-1", difficulty="HARD"
        )

        assert total == 1
        assert items[0]["id"] == "hard"

    async def test_source_filter_is_passed_through(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("ai", "prep-1", source="AI_GENERATED")
        repo.add_question("paper", "prep-1", source="PAST_PAPER")

        items, total = await exam_prep_service.search_question_bank(
            user_id=OWNER, prep_id="prep-1", source="PAST_PAPER"
        )

        assert total == 1
        assert items[0]["id"] == "paper"

    async def test_missing_metadata_is_returned_as_null(self, repo):
        """Questions banked before these columns existed have no metadata, and
        inventing a difficulty for them would be worse than showing none."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("legacy", "prep-1", difficulty=None, source=None)

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")

        assert items[0]["difficulty"] is None
        assert items[0]["source"] is None
        assert items[0]["sourceYear"] is None


# ---------------------------------------------------------------------------
# TestQuestionFlagging
# ---------------------------------------------------------------------------


class TestQuestionFlagging:
    """A flag is worth having only if it survives the session it was raised in."""

    async def test_flagging_records_the_flag(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        result = await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1", note="revisit the wording"
        )

        assert result["isFlagged"] is True
        assert result["note"] == "revisit the wording"
        assert (OWNER, "q1") in repo.flags

    async def test_note_is_optional(self, repo):
        """Requiring a reason would suppress the signal."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        result = await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1"
        )

        assert result["isFlagged"] is True
        assert result["note"] is None

    async def test_flagging_twice_is_idempotent(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        await exam_prep_service.flag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")
        await exam_prep_service.flag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")

        assert len(repo.flags) == 1

    async def test_reflagging_updates_the_note(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1", note="first"
        )
        result = await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1", note="second"
        )

        assert result["note"] == "second"
        assert len(repo.flags) == 1

    async def test_reflagging_without_a_note_keeps_the_existing_one(self, repo):
        """Tapping the flag again should not silently erase what was written."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1", note="keep me"
        )
        result = await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="q1"
        )

        assert result["note"] == "keep me"

    async def test_unflagging_removes_it(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")
        await exam_prep_service.flag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")

        await exam_prep_service.unflag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")

        assert repo.flags == {}

    async def test_unflagging_something_unflagged_succeeds(self, repo):
        """The caller wanted it unflagged, and it is."""
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        await exam_prep_service.unflag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")

    async def test_flag_state_appears_in_the_bank_listing(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("flagged", "prep-1")
        repo.add_question("plain", "prep-1")
        await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="flagged", note="why"
        )

        items, _ = await exam_prep_service.search_question_bank(user_id=OWNER, prep_id="prep-1")
        by_id = {item["id"]: item for item in items}

        assert by_id["flagged"]["isFlagged"] is True
        assert by_id["flagged"]["flagNote"] == "why"
        assert by_id["plain"]["isFlagged"] is False
        assert by_id["plain"]["flagNote"] is None

    async def test_flagged_only_filter(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("flagged", "prep-1")
        repo.add_question("plain", "prep-1")
        await exam_prep_service.flag_question(
            user_id=OWNER, prep_id="prep-1", question_id="flagged"
        )

        items, total = await exam_prep_service.search_question_bank(
            user_id=OWNER, prep_id="prep-1", flagged_only=True
        )

        assert total == 1
        assert items[0]["id"] == "flagged"

    async def test_one_learners_flag_is_invisible_to_another(self, repo):
        """Flags are personal. The join is scoped to the requesting learner."""
        repo.add_prep("prep-1", OWNER)
        repo.add_prep("prep-1", INTRUDER)  # same prep id, both can read their own view
        repo.add_question("q1", "prep-1")
        await exam_prep_service.flag_question(user_id=OWNER, prep_id="prep-1", question_id="q1")

        items, _ = await exam_prep_service.search_question_bank(user_id=INTRUDER, prep_id="prep-1")

        assert items[0]["isFlagged"] is False

    async def test_cannot_flag_a_question_in_another_users_preparation(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_question("q1", "prep-1")

        with pytest.raises(NotFoundError):
            await exam_prep_service.flag_question(
                user_id=INTRUDER, prep_id="prep-1", question_id="q1"
            )
        assert repo.flags == {}

    async def test_cannot_flag_a_question_from_a_different_preparation(self, repo):
        """Scoped to the parent, the same rule as everywhere else."""
        repo.add_prep("prep-1", OWNER)
        repo.add_prep("prep-2", OWNER)
        repo.add_question("q1", "prep-1")

        with pytest.raises(NotFoundError):
            await exam_prep_service.flag_question(user_id=OWNER, prep_id="prep-2", question_id="q1")

    async def test_cannot_flag_an_unknown_question(self, repo):
        repo.add_prep("prep-1", OWNER)

        with pytest.raises(NotFoundError):
            await exam_prep_service.flag_question(
                user_id=OWNER, prep_id="prep-1", question_id="nope"
            )
