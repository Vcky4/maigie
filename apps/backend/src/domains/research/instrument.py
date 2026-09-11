"""The educator questionnaire, and the only place that decides whether an answer is valid.

``instrument.json`` is compiled from the research Markdown in ``maigie-public`` by
``scripts/build-survey-instrument.mjs`` and copied here byte-for-byte. Two copies of one file is a
deliberate trade: the public site is a static build that cannot fetch a question bank at render time,
and the two repositories never share a checkout, so a shared import is not available. What keeps them
honest is that the copy is generated rather than typed, carries an ``instrumentVersion``, and is
compared against this endpoint by ``npm run check:instrument`` — the same arrangement as the pricing
catalogue.

**Why validation lives against the bank rather than in a schema.** Seventy-five questions with
per-question option lists and selection limits would be thousands of lines of Pydantic that restates
the instrument, and a restatement drifts. Here the bank *is* the schema: an option that does not exist
in the research document cannot be stored, and a limit stated once in the Markdown is enforced from
the same place it is displayed.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INSTRUMENT_PATH = Path(__file__).with_name("instrument.json")

#: Free-text ceilings by question type. The instrument distinguishes a short answer from a short
#: paragraph from a long paragraph, and the caps follow that distinction rather than applying one
#: number everywhere: Q71 is the single most valuable answer in the survey and should not be truncated
#: at the length appropriate to a contact detail.
TEXT_LIMITS = {"short_text": 500, "paragraph": 2_000, "long_text": 5_000}

#: The suffix under which an "Other: ____" elaboration is stored, e.g. `Q13_other`. A sibling key
#: rather than a nested object, so an export is one flat column per question and the free text is
#: never buried inside a JSON value an analyst has to unpack.
OTHER_SUFFIX = "_other"
MAX_OTHER_LENGTH = 300

CONSENT_QUESTION = "Q1"


class AnswerError(ValueError):
    """A rejected answer, carrying the question it belongs to."""

    def __init__(self, question_id: str, message: str):
        self.question_id = question_id
        super().__init__(f"{question_id}: {message}")


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """The parsed instrument. Cached — it is a static file read once per process."""
    return json.loads(_INSTRUMENT_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def questions_by_id() -> dict[str, dict[str, Any]]:
    return {q["id"]: q for q in load()["questions"]}


@lru_cache(maxsize=1)
def checksum() -> str:
    """A digest of the file as committed, for the cross-repository drift check.

    Over the raw bytes rather than a re-serialised structure: the point is to detect that the two
    repositories hold different files, and normalising before hashing would hide exactly the
    whitespace-and-ordering differences that indicate one side was edited by hand.
    """
    return hashlib.sha256(_INSTRUMENT_PATH.read_bytes()).hexdigest()


def version() -> int:
    return int(load()["instrumentVersion"])


def options_for(question: dict[str, Any]) -> list[dict[str, Any]]:
    """A question's own options, or the list it borrows (Q61 and Q62 reuse Q60's)."""
    borrowed = question["answer"].get("optionsFrom")
    if borrowed:
        return questions_by_id()[borrowed]["options"]
    return question["options"]


def consent_given(answers: dict[str, Any]) -> bool:
    """Whether the stored answers record an explicit yes to the consent question."""
    option = next(
        (o for o in questions_by_id()[CONSENT_QUESTION]["options"] if not o.get("endsSurvey")),
        None,
    )
    return option is not None and answers.get(CONSENT_QUESTION) == option["value"]


def is_visible(question: dict[str, Any], answers: dict[str, Any]) -> bool:
    """Whether a question's display condition is satisfied by the answers so far.

    An unanswered gate counts as *visible*. A respondent who skipped Q26 has not told us they run no
    live sessions, and treating silence as a disqualification would quietly drop the follow-ups from
    everyone who left an optional question blank.
    """
    rule = question.get("displayIf")
    if not rule:
        return True
    given = answers.get(rule["question"])
    if given is None:
        return True
    chosen = set(given) if isinstance(given, list) else {given}
    return not chosen.intersection(rule["notAnyOf"])


def _validate_choice_value(question: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str):
        raise AnswerError(question["id"], "expected an option value")
    allowed = {o["value"] for o in options_for(question)}
    if value not in allowed:
        raise AnswerError(question["id"], f'"{value}" is not one of its options')
    return value


def validate_answer(question_id: str, value: Any) -> Any:
    """Validate and normalise one answer. Raises `AnswerError` on anything the bank disallows.

    Selection *maxima* are enforced here; minima are not, because this runs on every autosave and a
    half-finished "select exactly three" is the normal state of a section in progress. `Q60`'s minimum
    is checked at submit, where it is a real requirement rather than a work-in-progress.
    """
    question = questions_by_id().get(question_id)
    if question is None:
        raise AnswerError(question_id, "not a question in this instrument")

    answer = question["answer"]
    kind = answer["type"]

    if kind == "single":
        return _validate_choice_value(question, value)

    if kind == "multi":
        if not isinstance(value, list):
            raise AnswerError(question_id, "expected a list of option values")
        seen: list[str] = []
        for item in value:
            checked = _validate_choice_value(question, item)
            if checked not in seen:
                seen.append(checked)
        limit = answer.get("maxSelections")
        if limit is not None and len(seen) > limit:
            raise AnswerError(question_id, f"select at most {limit}")
        return seen

    if kind == "scale":
        # Two scales carry a non-numeric escape ("Not applicable"). It is a legitimate answer, not a
        # refusal to answer: an educator whose learners would never pay cannot honestly place
        # themselves on a comfort scale, and rejecting the opt-out would push them into inventing a
        # number. Accepted as the option's own value, so it is distinguishable from both a score and a
        # skipped question.
        if answer.get("naOption") and value == answer["naOption"]:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise AnswerError(question_id, "expected a whole number")
        if not answer["min"] <= value <= answer["max"]:
            raise AnswerError(question_id, f'expected {answer["min"]}–{answer["max"]}')
        return value

    if kind in TEXT_LIMITS:
        if not isinstance(value, str):
            raise AnswerError(question_id, "expected text")
        text = value.strip()
        if len(text) > TEXT_LIMITS[kind]:
            raise AnswerError(question_id, f"at most {TEXT_LIMITS[kind]} characters")
        return text

    raise AnswerError(question_id, f'unsupported response type "{kind}"')


def validate_patch(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate one section's worth of answers, including any `Other` elaborations.

    An explicit `None` is how a client clears an answer — the respondent went back and unselected
    something — and is passed through so the caller can delete the key.
    """
    cleaned: dict[str, Any] = {}
    for key, value in changes.items():
        if key.endswith(OTHER_SUFFIX):
            base = key[: -len(OTHER_SUFFIX)]
            question = questions_by_id().get(base)
            if question is None:
                raise AnswerError(key, "not a question in this instrument")
            if not any(o.get("freeText") for o in options_for(question)):
                raise AnswerError(base, "has no Other option to elaborate on")
            if value is None:
                cleaned[key] = None
                continue
            if not isinstance(value, str):
                raise AnswerError(key, "expected text")
            cleaned[key] = value.strip()[:MAX_OTHER_LENGTH]
            continue

        if value is None:
            cleaned[key] = None
            continue
        cleaned[key] = validate_answer(key, value)
    return cleaned


def prune_hidden(answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop answers to questions the respondent's own earlier answers say they never saw.

    This exists because a respondent can go back. Someone who answers Q26 with a live-session tool,
    fills in Q27 and Q28, then returns and changes Q26 to "I do not conduct live online sessions"
    leaves two answers about a setup they have just told us they do not have. Erroring would be
    hostile to a legitimate edit, so the stale answers are removed instead — the alternative is
    analysis on a population the gate was supposed to exclude.
    """
    kept = dict(answers)
    removed: list[str] = []
    # In question order, so a chain of gates resolves against already-pruned answers rather than
    # against values that are themselves about to be removed.
    for question in load()["questions"]:
        qid = question["id"]
        if is_visible(question, kept):
            continue
        for key in (qid, f"{qid}{OTHER_SUFFIX}"):
            if key in kept:
                del kept[key]
                removed.append(key)
    return kept, removed


def describe_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Group a response into sections with prompts and option labels, for human review.

    Stored answers are option *slugs* keyed by question id — the right shape for analysis and the
    wrong one for reading. Rendering happens here rather than in the admin client because the question
    bank lives here: shipping a third copy of it into the admin app so it could look up its own labels
    would add a file that can drift from two others, to solve a problem the server can answer in one
    response.

    Only answered questions appear. A reviewer scanning a partial response wants the twenty things the
    respondent said, not those twenty buried in fifty-five "not answered" rows.
    """
    labels_by_question: dict[str, dict[str, str]] = {}
    described: list[dict[str, Any]] = []

    for section in load()["sections"]:
        items: list[dict[str, Any]] = []
        for question in load()["questions"]:
            if question["section"] != section["id"]:
                continue
            value = answers.get(question["id"])
            if value is None or (isinstance(value, str | list) and len(value) == 0):
                continue

            if question["id"] not in labels_by_question:
                labels_by_question[question["id"]] = {
                    o["value"]: o["label"] for o in options_for(question)
                }
            labels = labels_by_question[question["id"]]
            kind = question["answer"]["type"]

            if kind == "multi":
                rendered = [labels.get(v, v) for v in value]
            elif kind == "single":
                rendered = [labels.get(value, value)]
            elif kind == "scale":
                # The scale's own wording, so a stored `4` reads as "4 — Very relevant" rather than a
                # bare number a reviewer has to go and look up. Scale option slugs are derived from
                # their labels (`4_very_relevant`), not from the number, so the lookup is by the label's
                # leading digit — and an opt-out answer is already a slug and matches directly.
                if isinstance(value, str):
                    rendered = [labels.get(value, value)]
                else:
                    rendered = [
                        next(
                            (label for label in labels.values() if label.startswith(f"{value} ")),
                            str(value),
                        )
                    ]
            else:
                rendered = []

            items.append(
                {
                    "questionId": question["id"],
                    "prompt": question["prompt"],
                    "kind": kind,
                    "values": rendered,
                    "text": value if kind in TEXT_LIMITS else None,
                    "otherText": answers.get(f'{question["id"]}{OTHER_SUFFIX}'),
                }
            )

        if items:
            described.append(
                {
                    "id": section["id"],
                    "number": section["number"],
                    "title": section["title"],
                    "items": items,
                }
            )
    return described


def missing_required(answers: dict[str, Any]) -> list[str]:
    """Required questions that are visible to this respondent but unanswered."""
    missing = []
    for question in load()["questions"]:
        if not question.get("required") or not is_visible(question, answers):
            continue
        value = answers.get(question["id"])
        if value is None or (isinstance(value, str | list) and len(value) == 0):
            missing.append(question["id"])
    return missing


def unmet_minimums(answers: dict[str, Any]) -> list[str]:
    """Answered questions that specify a minimum number of selections and fall short.

    Checked only at submit. Q60 is the one question in the instrument that says *exactly* three, and
    an under-filled Q60 makes its own analysis (and Q61's borrowed list) unusable.
    """
    short = []
    for question in load()["questions"]:
        minimum = question["answer"].get("minSelections")
        if not minimum or not is_visible(question, answers):
            continue
        value = answers.get(question["id"])
        if isinstance(value, list) and 0 < len(value) < minimum:
            short.append(question["id"])
    return short
