"""Unit tests for the quiz engine's pure logic (no DB required).

Covers the three things Phase 4 made load-bearing:

- the answer-key disclosure boundary (Decision C), which is per *answered
  question* rather than per session;
- generated-question validation, which is what stops an unscorable question from
  counting against a learner;
- topic attribution, which per-topic mastery and therefore readiness depend on.

The security behaviours that need repository interaction live in
``test_quiz_engine_scoring.py``.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timezone
from types import SimpleNamespace

from src.domains.personal_learning import models
from src.domains.personal_learning.services.quiz_engine import (
    _build_quiz_response,
    _check_answer_correctness,
    _resolve_topic_id,
    _suggest_next_step,
    _usable_question,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question(qid: str = "q1", *, key: str = "the right one", explanation: str = "because reasons"):
    """A banked question. Carries no order: that lives on the session link."""
    return SimpleNamespace(
        id=qid,
        prep_id="prep-1",
        question_text=f"Question {qid}?",
        question_type="MULTIPLE_CHOICE",
        options=[key, "wrong a", "wrong b", "wrong c"],
        prep_topic_id="topic-1",
        correct_answer=key,
        explanation=explanation,
    )


def _session(status: str):
    return SimpleNamespace(
        id="quiz-1",
        user_id="user-1",
        prep_id="prep-1",
        mode="FULL_PRACTICE",
        topic_id=None,
        status=status,
        total_questions=2,
        correct_count=1,
        score_percentage=50.0,
        duration_seconds=60,
        completed_at=NOW if status == "COMPLETED" else None,
        created_at=NOW,
    )


def _answer(question_id: str, *, correct: bool = True):
    return SimpleNamespace(
        question_id=question_id,
        user_answer="the right one",
        is_correct=correct,
        time_taken_seconds=9,
        created_at=NOW,
    )


def _wire(status: str, questions, answers):
    """Build the response and push it through the real model, as the route does.

    Questions are paired with their position in the session, because order now
    belongs to the session that asked the question rather than to the banked
    question itself.
    """
    ordered = [(question, index) for index, question in enumerate(questions)]
    built = _build_quiz_response(_session(status), ordered, answers)
    dumped = models.QuizSessionResponse.model_validate(built).model_dump(by_alias=True)
    return {item["id"]: item for item in dumped["questions"]}


# ---------------------------------------------------------------------------
# TestAnswerKeyDisclosure
# ---------------------------------------------------------------------------


class TestAnswerKeyDisclosure:
    """The boundary from Decision C: a key is disclosed only for a question the
    learner has already committed an answer to, or once the session is done."""

    def test_fresh_session_discloses_nothing(self):
        questions = [_question("q1"), _question("q2")]
        wire = _wire("GENERATING", questions, [])
        assert all(q["correctAnswer"] is None for q in wire.values())
        assert all(q["explanation"] is None for q in wire.values())

    def test_in_progress_session_discloses_nothing_before_answering(self):
        wire = _wire("IN_PROGRESS", [_question("q1")], [])
        assert wire["q1"]["correctAnswer"] is None
        assert wire["q1"]["explanation"] is None

    def test_answered_question_discloses_its_key_mid_session(self):
        """Teaching in small steps: the explanation survives a resume."""
        questions = [_question("q1", key="alpha", explanation="alpha wins")]
        wire = _wire("IN_PROGRESS", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] == "alpha"
        assert wire["q1"]["explanation"] == "alpha wins"

    def test_unanswered_sibling_stays_sealed(self):
        """The boundary is per question, not per session."""
        questions = [
            _question("q1", key="alpha", explanation="alpha wins"),
            _question("q2", key="beta", explanation="beta wins"),
        ]
        wire = _wire("IN_PROGRESS", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] == "alpha"
        assert wire["q2"]["correctAnswer"] is None
        assert wire["q2"]["explanation"] is None

    def test_unanswered_explanation_absent_from_payload_entirely(self):
        """Asserted on the explanation, not the answer text.

        The correct answer's *text* is necessarily present, because it is one of
        the options the learner picks from. The explanation exists nowhere but the
        answer key, so it is the honest canary for a leak.
        """
        questions = [_question("q2", key="beta", explanation="a very distinctive rationale")]
        wire = _wire("IN_PROGRESS", questions, [])
        assert "a very distinctive rationale" not in str(wire)

    def test_completed_session_discloses_everything(self):
        questions = [
            _question("q1", key="alpha", explanation="alpha wins"),
            _question("q2", key="beta", explanation="beta wins"),
        ]
        wire = _wire("COMPLETED", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] == "alpha"
        # Revealed even though it was never answered, so review is complete.
        assert wire["q2"]["correctAnswer"] == "beta"
        assert wire["q2"]["explanation"] == "beta wins"

    def test_failed_session_discloses_nothing(self):
        wire = _wire("FAILED", [_question("q1")], [])
        assert wire["q1"]["correctAnswer"] is None

    def test_learner_own_answer_is_always_attached(self):
        wire = _wire("IN_PROGRESS", [_question("q1")], [_answer("q1", correct=False)])
        assert wire["q1"]["userAnswer"] == "the right one"
        assert wire["q1"]["isCorrect"] is False
        assert wire["q1"]["timeTakenSeconds"] == 9

    def test_question_body_is_never_withheld(self):
        """Withholding the key must not withhold what the learner needs to answer."""
        wire = _wire("IN_PROGRESS", [_question("q1")], [])
        assert wire["q1"]["questionText"] == "Question q1?"
        assert len(wire["q1"]["options"]) == 4
        assert wire["q1"]["questionType"] == "MULTIPLE_CHOICE"

    def test_option_order_is_preserved(self):
        """Reordering options to hide the answer would break index-based answers."""
        question = _question("q1", key="alpha")
        wire = _wire("IN_PROGRESS", [question], [])
        assert wire["q1"]["options"] == question.options


# ---------------------------------------------------------------------------
# TestUsableQuestion
# ---------------------------------------------------------------------------


BASE_CANDIDATE = {
    "questionText": "What is the null hypothesis?",
    "questionType": "MULTIPLE_CHOICE",
    "options": ["no effect", "a guess", "a p-value", "a sample"],
    "correctAnswer": "no effect",
    "explanation": "It asserts no effect.",
}


class TestUsableQuestion:
    """Rejecting an unscorable question beats persisting one the learner cannot win."""

    def test_valid_candidate_is_normalized(self):
        result = _usable_question(BASE_CANDIDATE)
        assert result is not None
        assert result["question_text"] == "What is the null hypothesis?"
        assert result["correct_answer"] == "no effect"
        assert result["options"] == ["no effect", "a guess", "a p-value", "a sample"]

    def test_missing_question_text_rejected(self):
        assert (
            _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "questionText"})
            is None
        )

    def test_blank_question_text_rejected(self):
        assert _usable_question({**BASE_CANDIDATE, "questionText": "   "}) is None

    def test_empty_correct_answer_rejected(self):
        """The column is NOT NULL and used to default to "", making the question
        unanswerable-correctly while still counting against the learner."""
        assert _usable_question({**BASE_CANDIDATE, "correctAnswer": ""}) is None

    def test_missing_correct_answer_rejected(self):
        assert (
            _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "correctAnswer"})
            is None
        )

    def test_multiple_choice_answer_not_among_options_rejected(self):
        """The right answer is not on offer, so the question can only be wrong."""
        assert _usable_question({**BASE_CANDIDATE, "correctAnswer": "something else"}) is None

    def test_multiple_choice_needs_at_least_two_options(self):
        assert _usable_question({**BASE_CANDIDATE, "options": ["no effect"]}) is None

    def test_multiple_choice_without_options_rejected(self):
        assert _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "options"}) is None

    def test_non_dict_rejected(self):
        assert _usable_question("not a question") is None
        assert _usable_question(None) is None
        assert _usable_question(["a", "list"]) is None

    def test_answer_matching_option_case_insensitively_is_accepted(self):
        """Providers vary in casing; that is not a reason to drop a good question."""
        assert _usable_question({**BASE_CANDIDATE, "correctAnswer": "No Effect"}) is not None

    def test_short_answer_without_options_is_accepted(self):
        result = _usable_question(
            {
                "questionText": "Define p-value.",
                "questionType": "SHORT_ANSWER",
                "correctAnswer": "42",
            }
        )
        assert result is not None
        assert result["options"] is None

    def test_blank_options_are_stripped(self):
        result = _usable_question({**BASE_CANDIDATE, "options": ["no effect", "  ", "a guess", ""]})
        assert result is not None
        assert result["options"] == ["no effect", "a guess"]

    def test_missing_explanation_is_allowed(self):
        result = _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "explanation"})
        assert result is not None
        assert result["explanation"] is None

    def test_question_type_defaults_to_multiple_choice(self):
        result = _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "questionType"})
        assert result is not None
        assert result["question_type"] == "MULTIPLE_CHOICE"


# ---------------------------------------------------------------------------
# TestResolveTopicId
# ---------------------------------------------------------------------------


class TestResolveTopicId:
    """Attribution feeds per-topic mastery, so a silent miss degrades readiness."""

    TOPICS = [
        SimpleNamespace(id="t1", title="Hypothesis Testing"),
        SimpleNamespace(id="t2", title="Regression"),
        SimpleNamespace(id="t3", title="Sampling"),
    ]

    def test_topic_number_resolves(self):
        assert _resolve_topic_id({"topicNumber": 2}, self.TOPICS) == "t2"

    def test_topic_number_is_one_based(self):
        assert _resolve_topic_id({"topicNumber": 1}, self.TOPICS) == "t1"

    def test_topic_number_as_string_resolves(self):
        assert _resolve_topic_id({"topicNumber": "3"}, self.TOPICS) == "t3"

    def test_zero_and_negative_numbers_do_not_resolve(self):
        assert _resolve_topic_id({"topicNumber": 0}, self.TOPICS) is None
        assert _resolve_topic_id({"topicNumber": -1}, self.TOPICS) is None

    def test_out_of_range_number_does_not_resolve(self):
        assert _resolve_topic_id({"topicNumber": 99}, self.TOPICS) is None

    def test_garbage_number_falls_through_to_title(self):
        assert (
            _resolve_topic_id({"topicNumber": "nonsense", "topicTitle": "Regression"}, self.TOPICS)
            == "t2"
        )

    def test_title_fallback_is_case_and_space_insensitive(self):
        assert _resolve_topic_id({"topicTitle": "  regression "}, self.TOPICS) == "t2"

    def test_paraphrased_title_is_unattributed(self):
        """The old title-matching behaviour: this is exactly what used to be lost
        silently, and why the prompt now asks for a number."""
        assert _resolve_topic_id({"topicTitle": "Regression Analysis"}, self.TOPICS) is None

    def test_single_target_topic_is_unambiguous(self):
        """TOPIC_FOCUS practice: whatever came back, there is only one answer."""
        assert _resolve_topic_id({}, self.TOPICS[:1]) == "t1"
        assert _resolve_topic_id({"topicNumber": 42}, self.TOPICS[:1]) == "t1"

    def test_no_topics_gives_none(self):
        assert _resolve_topic_id({"topicNumber": 1}, []) is None

    def test_non_dict_candidate_gives_none(self):
        assert _resolve_topic_id("nope", self.TOPICS) is None

    def test_number_takes_precedence_over_a_conflicting_title(self):
        resolved = _resolve_topic_id({"topicNumber": 1, "topicTitle": "Regression"}, self.TOPICS)
        assert resolved == "t1"


# ---------------------------------------------------------------------------
# TestCheckAnswerCorrectness
# ---------------------------------------------------------------------------


class TestCheckAnswerCorrectness:
    """Pre-existing matcher, tested because every score depends on it."""

    OPTIONS = ["Paris", "London", "Berlin", "Madrid"]

    def test_exact_match(self):
        assert _check_answer_correctness("Paris", "Paris", self.OPTIONS) is True

    def test_case_and_whitespace_insensitive(self):
        assert _check_answer_correctness("  paris ", "Paris", self.OPTIONS) is True

    def test_wrong_option_is_incorrect(self):
        assert _check_answer_correctness("London", "Paris", self.OPTIONS) is False

    def test_letter_index_matches_option_text(self):
        assert _check_answer_correctness("A", "Paris", self.OPTIONS) is True
        assert _check_answer_correctness("B", "Paris", self.OPTIONS) is False

    def test_numeric_index_matches_option_text(self):
        assert _check_answer_correctness("0", "Paris", self.OPTIONS) is True
        assert _check_answer_correctness("2", "Paris", self.OPTIONS) is False

    def test_prefixed_option_text(self):
        assert _check_answer_correctness("A. Paris", "Paris", self.OPTIONS) is True
        assert _check_answer_correctness("A) Paris", "Paris", self.OPTIONS) is True

    def test_stored_key_as_letter_matches_option_text(self):
        assert _check_answer_correctness("Paris", "A", self.OPTIONS) is True

    def test_short_answer_substring_tolerance(self):
        assert (
            _check_answer_correctness("the mitochondria produces energy", "mitochondria", None)
            is True
        )

    def test_short_answer_unrelated_is_incorrect(self):
        assert _check_answer_correctness("chloroplast", "mitochondria", None) is False


# ---------------------------------------------------------------------------
# TestSuggestNextStep
# ---------------------------------------------------------------------------


class TestSuggestNextStep:
    def test_no_weak_areas_is_encouraging(self):
        assert "Full Practice" in _suggest_next_step([])

    def test_single_weak_area_is_named(self):
        result = _suggest_next_step(["Regression"])
        assert "Regression" in result

    def test_many_weak_areas_are_truncated_to_three(self):
        result = _suggest_next_step(["A", "B", "C", "D"])
        assert "D" not in result


# ---------------------------------------------------------------------------
# TestQuestionMetadata
# ---------------------------------------------------------------------------


class TestQuestionMetadataNormalization:
    """Metadata from the generator is normalized, never trusted."""

    def test_recognised_difficulty_is_kept(self):
        for value in ("EASY", "MEDIUM", "HARD"):
            result = _usable_question({**BASE_CANDIDATE, "difficulty": value})
            assert result["difficulty"] == value

    def test_difficulty_is_upper_cased(self):
        assert _usable_question({**BASE_CANDIDATE, "difficulty": "hard"})["difficulty"] == "HARD"

    def test_unrecognised_difficulty_is_dropped_not_stored(self):
        """A badge reading "quite hard" is worse than no badge: the client would
        have to render it."""
        for value in ("quite hard", "Level 4", "IMPOSSIBLE", "", 7):
            result = _usable_question({**BASE_CANDIDATE, "difficulty": value})
            assert result["difficulty"] is None

    def test_missing_difficulty_is_none(self):
        assert _usable_question(BASE_CANDIDATE)["difficulty"] is None

    def test_exam_tip_is_kept_and_trimmed(self):
        result = _usable_question({**BASE_CANDIDATE, "examTip": "  Watch the wording.  "})
        assert result["exam_tip"] == "Watch the wording."

    def test_exam_tip_is_capped(self):
        """ "One sentence" is a request, not a guarantee."""
        result = _usable_question({**BASE_CANDIDATE, "examTip": "x" * 5000})
        assert len(result["exam_tip"]) == 500

    def test_blank_exam_tip_becomes_none(self):
        assert _usable_question({**BASE_CANDIDATE, "examTip": "   "})["exam_tip"] is None

    def test_missing_exam_tip_is_none(self):
        assert _usable_question(BASE_CANDIDATE)["exam_tip"] is None

    def test_generator_cannot_declare_its_own_provenance(self):
        """`source` is not among the normalized fields, so a model claiming its
        output came from a past paper cannot make that stick."""
        result = _usable_question({**BASE_CANDIDATE, "source": "PAST_PAPER", "sourceYear": 2019})
        assert "source" not in result
        assert "source_year" not in result


class TestExamTipDisclosure:
    """The tip follows the answer key, not the difficulty badge."""

    def _question_with_metadata(self, qid="q1"):
        return SimpleNamespace(
            id=qid,
            prep_id="prep-1",
            question_text="Question?",
            question_type="MULTIPLE_CHOICE",
            options=["right", "wrong a", "wrong b", "wrong c"],
            prep_topic_id="topic-1",
            correct_answer="right",
            explanation="because",
            difficulty="HARD",
            exam_tip="TIP_CANARY",
        )

    def test_difficulty_is_shown_before_answering(self):
        """Difficulty describes the question, not its answer."""
        wire = _wire("IN_PROGRESS", [self._question_with_metadata()], [])
        assert wire["q1"]["difficulty"] == "HARD"

    def test_exam_tip_is_withheld_before_answering(self):
        wire = _wire("IN_PROGRESS", [self._question_with_metadata()], [])
        assert wire["q1"]["examTip"] is None
        assert "TIP_CANARY" not in str(wire)

    def test_exam_tip_is_revealed_once_answered(self):
        wire = _wire("IN_PROGRESS", [self._question_with_metadata()], [_answer("q1")])
        assert wire["q1"]["examTip"] == "TIP_CANARY"

    def test_exam_tip_is_revealed_on_a_completed_session(self):
        wire = _wire("COMPLETED", [self._question_with_metadata()], [])
        assert wire["q1"]["examTip"] == "TIP_CANARY"
