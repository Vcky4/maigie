"""Grading and summarising a topic's end-of-lesson knowledge check.

The check itself lives as JSON on `Topic.knowledgeCheck`; the learner's answers live as rows in
`TopicCheckAttempt`, one per press of `Check answer`. This module is the part between them: it finds the
chosen option in the stored check, decides whether it was right, and reduces a learner's attempts to the
handful of facts a review needs.

## Why grading happens here and not in the browser

The reader already knows the answer — `correct` ships with the choices so the verdict and explanation can
appear the instant the learner answers, with no round trip to fail. That makes the published key useless
as the basis of a *record*: a stored result taken from the page under test is a result the page chose.
So the endpoint accepts a choice id and nothing else, and the verdict below is the only one written.

## Why the summary is computed rather than stored

Four counters on the topic would have to be kept in step with the rows by every writer, and there is
exactly one read per lesson open. Deriving from the rows cannot drift.
"""

from __future__ import annotations

from typing import Any

from .. import models


def find_choice(check: dict[str, Any] | None, choice_id: str) -> dict[str, Any] | None:
    """The chosen option as the stored check defines it, or None if the check has no such option.

    A missing choice is a real case rather than a client bug: a lesson regenerated between the page
    loading and the learner answering has a different set of options, and the id they clicked no longer
    exists. The caller refuses the attempt rather than recording a wrong answer to a question that was
    never asked.
    """
    if not isinstance(check, dict):
        return None
    choices = check.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if isinstance(choice, dict) and choice.get("id") == choice_id:
            return choice
    return None


def grade(choice: dict[str, Any]) -> bool:
    """Whether a stored choice is the correct one.

    Truthiness rather than `is True`: the value comes out of a JSON column written by generation, and a
    check that arrived with `"correct": 1` is still a check with a correct answer.
    """
    return bool(choice.get("correct"))


def summarise(attempts: list[Any]) -> models.TopicCheckSummary:
    """Reduce a learner's attempts at one check to what a review can act on.

    `attempts` is expected oldest first, which is what the repository guarantees, because the first
    attempt is the only one taken before the answer was revealed.

    `needsRevisit` stays true after a later attempt succeeds. That is the point of keeping every row:
    passing on the second try does not undo not knowing it on the first, and a signal that erases itself
    the moment the learner clicks the right answer would be a signal that never fires.
    """
    if not attempts:
        return models.TopicCheckSummary()

    incorrect = sum(1 for attempt in attempts if not attempt.correct)
    last = attempts[-1]

    return models.TopicCheckSummary(
        attempts=len(attempts),
        incorrect_attempts=incorrect,
        first_attempt_correct=bool(attempts[0].correct),
        passed=any(attempt.correct for attempt in attempts),
        last_attempt_at=last.created_at,
        last_choice_id=last.choice_id,
        needs_revisit=incorrect > 0,
    )


def explanation_of(check: dict[str, Any] | None) -> str:
    """The check's explanation, or an empty string when it has none.

    Empty rather than a stand-in sentence: the reader omits the block, which is better than printing a
    reason that was never written for this question.
    """
    if not isinstance(check, dict):
        return ""
    explanation = check.get("explanation")
    return explanation if isinstance(explanation, str) else ""


def question_of(check: dict[str, Any] | None) -> str:
    """The question as currently stored, for snapshotting onto an attempt."""
    if not isinstance(check, dict):
        return ""
    question = check.get("question")
    return question if isinstance(question, str) else ""
