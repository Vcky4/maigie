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
from src.domains.personal_learning.services import quiz_engine
from src.domains.personal_learning.services.quiz_engine import (
    _build_quiz_response,
    _check_answer_correctness,
    _eliminable_option,
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


def _link(order_index: int, *, hints_used: int = 0):
    """The session-to-question link: position and hints taken live here."""
    return SimpleNamespace(order_index=order_index, hint_count=hints_used)


def _wire(
    status: str,
    questions,
    answers,
    hints: dict[str, int] | None = None,
    topic_titles: dict[str, str] | None = None,
):
    """Build the response and push it through the real model, as the route does.

    Questions are paired with their session link, because everything
    session-specific — position, hints taken — belongs to the link rather than to
    the banked question.
    """
    ordered = [
        (question, _link(index, hints_used=hints.get(question.id, 0) if hints else 0))
        for index, question in enumerate(questions)
    ]
    built = _build_quiz_response(_session(status), ordered, answers, topic_titles)
    dumped = models.QuizSessionResponse.model_validate(built).model_dump(by_alias=True)
    return {item["id"]: item for item in dumped["questions"]}


# ---------------------------------------------------------------------------
# TestQuestionProvenanceDisclosure
# ---------------------------------------------------------------------------


class TestQuestionProvenanceDisclosure:
    """Provenance and topic label are shown from the start; the key still is not.

    The runner badges a question with its topic and, for past papers, its year.
    Both sit on the same side of the disclosure boundary as difficulty: knowing a
    question came from a 2025 paper on hypothesis testing says nothing about which
    option is correct.
    """

    def _question_with_provenance(self, qid="q1"):
        question = _question(qid)
        question.difficulty = "MEDIUM"
        question.source = "PAST_PAPER"
        question.source_year = 2025
        question.exam_tip = "Compare p with alpha first."
        return question

    def test_provenance_is_visible_before_answering(self):
        question = self._question_with_provenance()
        wire = _wire("IN_PROGRESS", [question], [], topic_titles={"topic-1": "Hypothesis testing"})
        item = wire["q1"]
        assert item["source"] == "PAST_PAPER"
        assert item["sourceYear"] == 2025
        assert item["difficulty"] == "MEDIUM"
        assert item["prepTopicTitle"] == "Hypothesis testing"

    def test_provenance_does_not_come_with_the_key(self):
        # The guard that matters: adding these fields must not have widened
        # disclosure. The exam tip stays with the key, the answer stays hidden.
        question = self._question_with_provenance()
        wire = _wire("IN_PROGRESS", [question], [], topic_titles={"topic-1": "Hypothesis testing"})
        item = wire["q1"]
        assert item["correctAnswer"] is None
        assert item["explanation"] is None
        assert item["examTip"] is None

    def test_an_unattributed_question_has_no_topic_title(self):
        question = self._question_with_provenance()
        question.prep_topic_id = None
        wire = _wire("IN_PROGRESS", [question], [], topic_titles={"topic-1": "Hypothesis testing"})
        assert wire["q1"]["prepTopicTitle"] is None

    def test_a_missing_title_map_is_not_an_error(self):
        # `list_prep_quizzes` builds sessions with no questions and no titles.
        wire = _wire("IN_PROGRESS", [self._question_with_provenance()], [])
        assert wire["q1"]["prepTopicTitle"] is None


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
        # Membership, not order: option order is deliberately shuffled so the
        # answer's position is not predictable. Asserting the original order would
        # be asserting the bias.
        assert sorted(result["options"]) == sorted(
            ["no effect", "a guess", "a p-value", "a sample"]
        )
        assert result["correct_answer"] in result["options"]

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
        assert sorted(result["options"]) == sorted(["no effect", "a guess"])

    def test_missing_explanation_is_allowed(self):
        result = _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "explanation"})
        assert result is not None
        assert result["explanation"] is None

    def test_question_type_defaults_to_multiple_choice(self):
        result = _usable_question({k: v for k, v in BASE_CANDIDATE.items() if k != "questionType"})
        assert result is not None
        assert result["question_type"] == "MULTIPLE_CHOICE"


# ---------------------------------------------------------------------------
# TestDefaultQuestionCount
# ---------------------------------------------------------------------------


class TestDefaultQuestionCount:
    """How long a session is, when the learner has not said.

    Reported as "why are they all 5 questions": the client was sending a hardcoded
    5 on every request, so the server's own sizing never ran and every mode looked
    identical regardless of how much material there was.
    """

    def test_it_scales_with_the_material(self):
        assert quiz_engine.default_question_count("WEAK_AREAS", 3) == 6
        assert quiz_engine.default_question_count("WEAK_AREAS", 5) == 10

    def test_each_mode_caps_it_differently(self):
        # A check-in stays short even with a lot of material; an exam section does
        # not, because length is part of what makes it an exam section.
        many = 40
        assert quiz_engine.default_question_count("QUICK_REVIEW", many) == 10
        assert quiz_engine.default_question_count("WEAK_AREAS", many) == 12
        assert quiz_engine.default_question_count("PAST_PAPER_SIM", many) == 20

    def test_a_single_topic_drill_is_not_two_questions(self):
        """The reason the cap is per mode rather than global."""
        assert quiz_engine.default_question_count("TOPIC_FOCUS", 1) == 5

    def test_nothing_is_shorter_than_the_floor(self):
        assert quiz_engine.default_question_count("QUICK_REVIEW", 1) == 5
        assert quiz_engine.default_question_count("QUICK_REVIEW", 0) == 5
        assert quiz_engine.default_question_count("QUICK_REVIEW", -3) == 5

    def test_an_unknown_mode_still_gets_a_sane_size(self):
        assert quiz_engine.default_question_count("SOMETHING_ELSE", 20) == 12

    def test_the_mode_is_matched_case_insensitively(self):
        assert quiz_engine.default_question_count("past_paper_sim", 40) == 20

    def test_duration_follows_the_count(self):
        for count in (5, 8, 12, 20):
            assert quiz_engine.estimated_minutes(count) == count * 2


# ---------------------------------------------------------------------------
# TestOptionOrder
# ---------------------------------------------------------------------------


class TestOptionOrder:
    """The correct answer's position must not be predictable.

    Reported from real use as "seems all answers are option B". Measured across the
    banked questions: A 44%, B 40%, C 15%, and D never correct — so guessing A or B
    scored about 85% while knowing nothing, and D could be discarded on sight. The
    score was measuring position rather than knowledge.
    """

    OPTIONS = ["right", "wrong a", "wrong b", "wrong c"]

    def _batch(self, size: int, option_count: int = 4) -> list[dict]:
        options = ["right"] + [f"wrong {index}" for index in range(option_count - 1)]
        return [
            {
                "question_type": "MULTIPLE_CHOICE",
                "options": list(options),
                "correct_answer": "right",
            }
            for _ in range(size)
        ]

    def test_reordering_preserves_the_options(self):
        # The set must be identical: this reorders, it never drops or invents an
        # option.
        batch = self._batch(8)
        expected = sorted(batch[0]["options"])
        quiz_engine.balance_answer_positions(batch)
        for question in batch:
            assert sorted(question["options"]) == expected
            assert len(question["options"]) == 4

    def test_reordering_cannot_break_the_answer_key(self):
        """The key is stored as text, so reordering leaves it pointing at the same
        option. This is the property that makes any of this safe."""
        batch = self._batch(8)
        quiz_engine.balance_answer_positions(batch)
        for question in batch:
            assert question["correct_answer"] in question["options"]

    def test_the_other_options_keep_their_relative_order(self):
        """Only the answer moves.

        A generator often puts distractors in a deliberate sequence — ascending
        values, chronological events — and shuffling all four would make such a
        question incoherent to read.
        """
        batch = self._batch(12)
        quiz_engine.balance_answer_positions(batch)
        for question in batch:
            others = [option for option in question["options"] if option != "right"]
            assert others == ["wrong 0", "wrong 1", "wrong 2"]

    def test_a_shuffled_question_is_still_answerable(self):
        """End to end: normalise, then answer with the key. Correct either way."""
        candidate = {**BASE_CANDIDATE}
        for _ in range(50):
            normalized = _usable_question(candidate)
            assert normalized is not None
            assert (
                _check_answer_correctness(
                    normalized["correct_answer"],
                    normalized["correct_answer"],
                    normalized["options"],
                    question_type="MULTIPLE_CHOICE",
                )
                is True
            )

    def test_answering_by_letter_follows_the_shuffled_order(self):
        """A letter resolves against the stored order, so the client's A/B/C/D
        labels stay correct after shuffling."""
        normalized = _usable_question(BASE_CANDIDATE)
        assert normalized is not None
        options = normalized["options"]
        key_index = options.index(normalized["correct_answer"])
        key_letter = "ABCD"[key_index]

        assert (
            _check_answer_correctness(
                key_letter,
                normalized["correct_answer"],
                options,
                question_type="MULTIPLE_CHOICE",
            )
            is True
        )
        wrong_letter = next(letter for letter in "ABCD" if letter != key_letter)
        assert (
            _check_answer_correctness(
                wrong_letter,
                normalized["correct_answer"],
                options,
                question_type="MULTIPLE_CHOICE",
            )
            is False
        )

    def test_a_full_batch_is_exactly_even(self):
        """The point of balancing across the batch rather than per question.

        With a whole number of blocks the distribution is exactly 25% each — not
        25% on average, which is all independent shuffling can promise.
        """
        from collections import Counter

        batch = self._batch(40)
        quiz_engine.balance_answer_positions(batch)
        positions = Counter(q["options"].index("right") for q in batch)

        assert dict(positions) == {0: 10, 1: 10, 2: 10, 3: 10}

    def test_every_block_uses_every_position_once(self):
        """Evenness holds locally, not just in the total.

        A session is four or five questions long, so a batch that is even overall
        but clustered — AAAA then BBBB — would still be exploitable within a
        sitting. Each block of four uses each position exactly once.
        """
        batch = self._batch(16)
        quiz_engine.balance_answer_positions(batch)
        indexes = [q["options"].index("right") for q in batch]

        for start in range(0, 16, 4):
            assert sorted(indexes[start : start + 4]) == [0, 1, 2, 3]

    def test_a_short_session_cannot_stack_one_letter(self):
        """Five questions cannot put four answers on the same letter.

        This is the reported experience, and independent shuffling permits it: it is
        simply unlikely, and unlikely happens.
        """
        from collections import Counter

        for _ in range(200):
            batch = self._batch(5)
            quiz_engine.balance_answer_positions(batch)
            positions = Counter(q["options"].index("right") for q in batch)
            # Four distinct positions across five questions, so the most any letter
            # can take is two.
            assert max(positions.values()) <= 2
            assert len(positions) == 4

    def test_a_remainder_does_not_repeat_a_position(self):
        batch = self._batch(3)
        quiz_engine.balance_answer_positions(batch)
        indexes = [q["options"].index("right") for q in batch]
        assert len(set(indexes)) == 3

    def test_three_option_questions_are_balanced_separately(self):
        """A three-option question has no fourth slot, and mixing counts into one
        cycle would skew both groups."""
        from collections import Counter

        batch = self._batch(9, option_count=3)
        quiz_engine.balance_answer_positions(batch)
        positions = Counter(q["options"].index("right") for q in batch)
        assert dict(positions) == {0: 3, 1: 3, 2: 3}

    def test_mixed_option_counts_are_each_balanced(self):
        from collections import Counter

        batch = self._batch(8) + self._batch(6, option_count=3)
        quiz_engine.balance_answer_positions(batch)

        four = Counter(q["options"].index("right") for q in batch if len(q["options"]) == 4)
        three = Counter(q["options"].index("right") for q in batch if len(q["options"]) == 3)
        assert dict(four) == {0: 2, 1: 2, 2: 2, 3: 2}
        assert dict(three) == {0: 2, 1: 2, 2: 2}

    def test_generation_balances_what_it_persists(self):
        """The wiring: normalising a batch and balancing it produces even positions.

        Guards against the balancing step being skipped in `start_quiz`, which is
        the only place questions are created.
        """
        from collections import Counter

        candidates = [
            {
                **BASE_CANDIDATE,
                "questionText": f"Question {index}?",
            }
            for index in range(12)
        ]
        normalized = [_usable_question(candidate) for candidate in candidates]
        assert all(question is not None for question in normalized)
        quiz_engine.balance_answer_positions(normalized)

        positions = Counter(
            question["options"].index(question["correct_answer"]) for question in normalized
        )
        assert dict(positions) == {0: 3, 1: 3, 2: 3, 3: 3}

    def test_two_option_questions_are_left_alone_by_type(self):
        """`TRUE_FALSE` is not shuffled: the convention is a stable True/False
        order, and with two options there is no position to exploit beyond a coin
        flip."""
        result = _usable_question(
            {
                **BASE_CANDIDATE,
                "questionType": "TRUE_FALSE",
                "options": ["True", "False"],
                "correctAnswer": "True",
            }
        )
        assert result is not None
        assert result["options"] == ["True", "False"]


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

    # -- The substring strategy must not reach a choice question ------------
    #
    # Reported from real use: a learner answered a multiple-choice question
    # wrongly and it was recorded as correct. Options routinely extend one
    # another, and the substring fallback applied to every question type, so
    # picking a longer option containing the right one passed.

    EXTENDING_OPTIONS = [
        "It increases",
        "It increases then decreases",
        "It decreases",
        "It stays the same",
    ]

    def test_a_longer_wrong_option_is_not_correct(self):
        assert (
            _check_answer_correctness(
                "It increases then decreases",
                "It increases",
                self.EXTENDING_OPTIONS,
                question_type="MULTIPLE_CHOICE",
            )
            is False
        )

    def test_a_shorter_wrong_option_is_not_correct(self):
        assert (
            _check_answer_correctness(
                "It increases",
                "It increases then decreases",
                self.EXTENDING_OPTIONS,
                question_type="MULTIPLE_CHOICE",
            )
            is False
        )

    def test_the_right_option_is_still_correct(self):
        # The guard must not have broken the case it was protecting.
        assert (
            _check_answer_correctness(
                "It increases",
                "It increases",
                self.EXTENDING_OPTIONS,
                question_type="MULTIPLE_CHOICE",
            )
            is True
        )

    def test_letters_still_resolve_for_choice_questions(self):
        assert (
            _check_answer_correctness(
                "B",
                "It increases then decreases",
                self.EXTENDING_OPTIONS,
                question_type="MULTIPLE_CHOICE",
            )
            is True
        )

    def test_true_false_is_a_choice_question(self):
        assert (
            _check_answer_correctness(
                "False", "True", ["True", "False"], question_type="TRUE_FALSE"
            )
            is False
        )

    def test_free_text_keeps_its_tolerance(self):
        assert (
            _check_answer_correctness(
                "the mitochondria produces energy",
                "mitochondria",
                None,
                question_type="SHORT_ANSWER",
            )
            is True
        )

    def test_an_unknown_type_is_treated_as_free_text(self):
        # `None` means we do not know the answer is drawn from a closed set, and a
        # typed answer deserves the benefit of the doubt.
        assert (
            _check_answer_correctness(
                "the mitochondria produces energy", "mitochondria", None, question_type=None
            )
            is True
        )

    def test_a_choice_question_whose_key_is_not_an_option_is_never_correct(self):
        # Phase 4 rejects these at generation, but legacy rows exist. Marking such
        # an answer correct by substring would credit the learner for a question
        # that cannot be answered correctly at all.
        assert (
            _check_answer_correctness(
                "It increases",
                "It increases a lot",
                self.EXTENDING_OPTIONS,
                question_type="MULTIPLE_CHOICE",
            )
            is False
        )


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


# ---------------------------------------------------------------------------
# TestHintValidation
# ---------------------------------------------------------------------------


class TestHintValidation:
    """A hint that contains the answer is the answer key with a different label."""

    def test_a_good_hint_is_kept(self):
        result = _usable_question({**BASE_CANDIDATE, "hint": "Think about what is assumed."})
        assert result["hint_nudge"] == "Think about what is assumed."

    def test_a_hint_containing_the_answer_is_discarded(self):
        """Asking a model not to reveal the answer is not the same as it obeying."""
        result = _usable_question(
            {**BASE_CANDIDATE, "hint": "Remember that it means no effect at all."}
        )
        assert result["hint_nudge"] is None

    def test_the_answer_check_is_case_insensitive(self):
        result = _usable_question({**BASE_CANDIDATE, "hint": "It is about NO EFFECT."})
        assert result["hint_nudge"] is None

    def test_a_hint_is_trimmed(self):
        result = _usable_question({**BASE_CANDIDATE, "hint": "  Consider the assumption.  "})
        assert result["hint_nudge"] == "Consider the assumption."

    def test_a_hint_is_capped(self):
        """A long hint is an explanation wearing a different hat."""
        result = _usable_question({**BASE_CANDIDATE, "hint": "y" * 2000})
        assert len(result["hint_nudge"]) == 300

    def test_a_blank_hint_becomes_none(self):
        assert _usable_question({**BASE_CANDIDATE, "hint": "   "})["hint_nudge"] is None

    def test_a_missing_hint_is_none(self):
        """No hint is an acceptable state, not an error."""
        assert _usable_question(BASE_CANDIDATE)["hint_nudge"] is None

    def test_a_question_without_a_usable_hint_is_still_accepted(self):
        """A missing hint must not cost us the question."""
        result = _usable_question({**BASE_CANDIDATE, "hint": "It means no effect"})
        assert result is not None
        assert result["hint_nudge"] is None


# ---------------------------------------------------------------------------
# TestEliminableOption
# ---------------------------------------------------------------------------


class TestEliminableOption:
    """Level-2 hints remove one wrong option, deterministically."""

    def _q(self, options, answer):
        return SimpleNamespace(options=options, correct_answer=answer)

    def test_removes_a_wrong_option(self):
        removed = _eliminable_option(self._q(["right", "wrong a", "wrong b", "wrong c"], "right"))
        assert removed in {"wrong a", "wrong b", "wrong c"}

    def test_never_removes_the_correct_option(self):
        for _ in range(10):
            removed = _eliminable_option(
                self._q(["right", "wrong a", "wrong b", "wrong c"], "right")
            )
            assert removed != "right"

    def test_is_deterministic(self):
        """Otherwise repeated taps eliminate everything and reveal the answer."""
        question = self._q(["right", "wrong a", "wrong b", "wrong c"], "right")
        results = {_eliminable_option(question) for _ in range(10)}
        assert len(results) == 1

    def test_declines_when_only_two_options_remain(self):
        """Eliminating one of two leaves no choice at all."""
        assert _eliminable_option(self._q(["right", "wrong"], "right")) is None

    def test_declines_for_short_answer_questions(self):
        assert _eliminable_option(self._q(None, "42")) is None

    def test_matches_the_answer_case_insensitively(self):
        removed = _eliminable_option(self._q(["Right", "wrong a", "wrong b"], "right"))
        assert removed != "Right"


# ---------------------------------------------------------------------------
# TestHintStateOnRead
# ---------------------------------------------------------------------------


class TestHintStateOnRead:
    def test_hints_taken_are_reported(self):
        """A resumed session should not offer a hint the learner already took."""
        wire = _wire("IN_PROGRESS", [_question("q1")], [], hints={"q1": 2})
        assert wire["q1"]["hintsUsed"] == 2

    def test_no_hints_reports_zero(self):
        wire = _wire("IN_PROGRESS", [_question("q1")], [])
        assert wire["q1"]["hintsUsed"] == 0

    def test_hint_count_is_per_question(self):
        wire = _wire("IN_PROGRESS", [_question("q1"), _question("q2")], [], hints={"q1": 1})
        assert wire["q1"]["hintsUsed"] == 1
        assert wire["q2"]["hintsUsed"] == 0


# ---------------------------------------------------------------------------
# TestExamConditionDisclosure
# ---------------------------------------------------------------------------


class TestExamConditionDisclosure:
    """Reading an exam-simulation session mid-paper reveals nothing."""

    def _exam_session(self, status: str):
        session = _session(status)
        session.mode = "PAST_PAPER_SIM"
        return session

    def _wire_exam(self, status: str, questions, answers):
        ordered = [(question, _link(index)) for index, question in enumerate(questions)]
        built = _build_quiz_response(self._exam_session(status), ordered, answers)
        dumped = models.QuizSessionResponse.model_validate(built).model_dump(by_alias=True)
        return {item["id"]: item for item in dumped["questions"]}

    def test_an_answered_question_stays_sealed_mid_paper(self):
        """The opposite of every other mode, and deliberately so."""
        questions = [_question("q1", key="alpha", explanation="alpha wins")]
        wire = self._wire_exam("IN_PROGRESS", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] is None
        assert wire["q1"]["explanation"] is None

    def test_the_learners_own_answer_is_still_shown(self):
        """They may review what they put down; they may not see if it was right."""
        wire = self._wire_exam("IN_PROGRESS", [_question("q1")], [_answer("q1")])
        assert wire["q1"]["userAnswer"] == "the right one"

    def test_everything_is_revealed_once_the_paper_is_submitted(self):
        questions = [_question("q1", key="alpha"), _question("q2", key="beta")]
        wire = self._wire_exam("COMPLETED", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] == "alpha"
        assert wire["q2"]["correctAnswer"] == "beta"

    def test_a_normal_session_still_reveals_on_answering(self):
        questions = [_question("q1", key="alpha", explanation="alpha wins")]
        wire = _wire("IN_PROGRESS", questions, [_answer("q1")])
        assert wire["q1"]["correctAnswer"] == "alpha"


def test_topic_id_survives_the_repository_field_mapping():
    """`topicId` must reach the column, or per-topic mastery loses its attribution.

    An audit found all existing QuizSession rows with `topicId` NULL. That turned out to
    be legitimate — a quiz spanning every topic is stored with no single topic — but the
    mapping is a dict lookup that silently drops unknown keys, so a rename or a typo in
    `_map_quiz_session` would discard the value with no error anywhere. Per-topic mastery
    and therefore readiness both depend on it.
    """
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    mapped = repo._map_quiz_session(
        {
            "userId": "u-1",
            "prepId": "p-1",
            "mode": "PRACTICE",
            "topicId": "topic-1",
            "status": "GENERATING",
            "totalQuestions": 5,
        }
    )

    assert mapped["topic_id"] == "topic-1"
    assert mapped["user_id"] == "u-1"
    assert mapped["prep_id"] == "p-1"


def test_a_quiz_across_all_topics_maps_a_null_topic_id():
    """`topic_id=None` is the legitimate 'all topics' case, not a missing value."""
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    mapped = repo._map_quiz_session(
        {"userId": "u-1", "prepId": "p-1", "mode": "PRACTICE", "topicId": None}
    )

    assert "topic_id" in mapped
    assert mapped["topic_id"] is None
