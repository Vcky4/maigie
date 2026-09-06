"""Grading and summarising knowledge-check attempts.

The behaviour worth pinning is the distinction the table exists for: passing on a later attempt must not
erase having failed on the first. Every other assertion here protects a case that arises from the check
being mutable JSON rather than rows — a rewritten lesson, a missing key, a value that is not the type the
generator was asked for.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.domains.knowledge.services import topic_check_service as service

CHECK = {
    "question": "What does an engagement cue signal?",
    "explanation": "Engagement cues signal readiness to interact.",
    "choices": [
        {"id": "a", "label": "Readiness to interact", "correct": True},
        {"id": "b", "label": "Hunger", "correct": False},
        {"id": "c", "label": "Overstimulation"},
    ],
}


def attempt(correct: bool, *, choice_id: str = "a", minutes: int = 0):
    """A stand-in for a persisted attempt: the service reads three attributes and no more."""
    return SimpleNamespace(
        correct=correct,
        choice_id=choice_id,
        created_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes),
    )


# ---------------------------------------------------------------------------
# find_choice
# ---------------------------------------------------------------------------


def test_find_choice_returns_the_matching_option():
    assert service.find_choice(CHECK, "b") == {"id": "b", "label": "Hunger", "correct": False}


def test_find_choice_returns_none_for_an_unknown_id():
    """The real case: the lesson was regenerated after the page loaded, so the id is gone."""
    assert service.find_choice(CHECK, "z") is None


def test_find_choice_tolerates_a_malformed_check():
    for check in (None, {}, {"choices": None}, {"choices": "a, b"}, {"choices": ["a"]}):
        assert service.find_choice(check, "a") is None


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


def test_grade_reads_the_correct_flag():
    assert service.grade({"id": "a", "correct": True}) is True
    assert service.grade({"id": "b", "correct": False}) is False


def test_grade_treats_a_missing_flag_as_incorrect():
    """Only one choice carries `correct`, so the rest arrive without it."""
    assert service.grade({"id": "c", "label": "Overstimulation"}) is False


def test_grade_accepts_a_truthy_non_boolean():
    """The flag comes out of a JSON column; `1` is still a correct answer."""
    assert service.grade({"id": "a", "correct": 1}) is True


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------


def test_no_attempts_summarises_to_unattempted():
    summary = service.summarise([])
    assert summary.attempts == 0
    assert summary.first_attempt_correct is None
    assert summary.passed is False
    assert summary.needs_revisit is False
    assert summary.last_choice_id is None


def test_first_attempt_correct_needs_no_revisit():
    summary = service.summarise([attempt(True)])
    assert summary.attempts == 1
    assert summary.incorrect_attempts == 0
    assert summary.first_attempt_correct is True
    assert summary.passed is True
    assert summary.needs_revisit is False


def test_failing_then_passing_still_needs_a_revisit():
    """The reason this table exists.

    The learner is shown the correct answer the moment they submit, so a later correct attempt says
    they can repeat what they were told. `firstAttemptCorrect` is what measures understanding, and
    `needsRevisit` has to survive the pass or it would never fire for anyone who kept clicking.
    """
    summary = service.summarise([attempt(False, choice_id="b"), attempt(True, minutes=1)])
    assert summary.attempts == 2
    assert summary.incorrect_attempts == 1
    assert summary.first_attempt_correct is False
    assert summary.passed is True
    assert summary.needs_revisit is True


def test_repeated_failures_are_all_counted():
    summary = service.summarise(
        [
            attempt(False, choice_id="b"),
            attempt(False, choice_id="c", minutes=1),
            attempt(False, choice_id="b", minutes=2),
        ]
    )
    assert summary.incorrect_attempts == 3
    assert summary.passed is False
    assert summary.needs_revisit is True


def test_summary_reports_the_latest_answer():
    """What the reader restores from: an answered question must not reopen as unanswered."""
    summary = service.summarise(
        [attempt(False, choice_id="b"), attempt(True, choice_id="a", minutes=5)]
    )
    assert summary.last_choice_id == "a"
    assert summary.last_attempt_at == datetime(2026, 8, 17, 9, 5, tzinfo=UTC)


# ---------------------------------------------------------------------------
# explanation_of / question_of
# ---------------------------------------------------------------------------


def test_explanation_and_question_are_read_from_the_check():
    assert service.explanation_of(CHECK) == "Engagement cues signal readiness to interact."
    assert service.question_of(CHECK) == "What does an engagement cue signal?"


def test_explanation_and_question_are_empty_when_absent():
    """Empty rather than a stand-in, so the reader omits the block instead of inventing a reason."""
    for check in (None, {}, {"explanation": 12, "question": []}):
        assert service.explanation_of(check) == ""
        assert service.question_of(check) == ""
