"""Educator survey behaviour: consent, section autosave, submit, and contact separation.

The rules that are research requirements rather than engineering choices, all in one place:

* **No consent, no row.** A respondent who declines Q1 leaves nothing behind. Recording "this person
  said no" would itself be a record about a person who declined to be recorded.
* **A partial response is data.** Every section save persists, and abandonment is a state rather than
  a failure. Most respondents will not finish seventy-five questions, and the ones who stop mid-way
  have usually already answered the pain-point sections that carry the research value.
* **Contact details never enter the answer map.** Q75 is redirected into its own table on the way in
  (see `db_models`), so the analysis surface cannot accidentally carry an identity.
* **Stale answers are pruned, not rejected.** Going back and changing a gating answer is legitimate;
  the answers it invalidates are removed at submit.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

from .. import instrument
from ..db_models import EducatorSurveyResponse
from ..repository import educator_survey_repo

logger = logging.getLogger(__name__)

#: The question whose answer is a contact detail, held apart from the answer map.
CONTACT_QUESTION = "Q75"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


async def start_response(*, consent: str, honeypot: str | None = None) -> tuple[str, str]:
    """Open a response after recorded consent. Returns `(id, raw_token)`.

    The honeypot is checked before anything is written. It fails as a validation error rather than
    silently discarding: a real client never fills the field, so a filled one is either a bot or a
    client bug, and both are better surfaced than swallowed.
    """
    if honeypot:
        logger.info("Educator survey: rejected a submission that filled the honeypot")
        raise ValidationError("Could not start the questionnaire")

    consent_question = instrument.questions_by_id()[instrument.CONSENT_QUESTION]
    affirmative = next(o for o in consent_question["options"] if not o.get("endsSurvey"))
    if consent != affirmative["value"]:
        # Declining is a valid answer to Q1 and needs no row. The wizard ends the survey locally.
        raise ValidationError("The questionnaire requires consent to continue")

    raw, token_hash = _new_token()
    row = await educator_survey_repo.create(
        {
            "token_hash": token_hash,
            "answers": {instrument.CONSENT_QUESTION: affirmative["value"]},
            "instrument_version": instrument.version(),
            "status": "partial",
            "last_section": 0,
        }
    )
    logger.info("Educator survey started: id=%s version=%s", row.id, row.instrument_version)
    return row.id, raw


async def resolve(*, token: str) -> EducatorSurveyResponse:
    """Find a response by its resume token."""
    row = await educator_survey_repo.get_by_token_hash(hash_token(token))
    if row is None:
        raise NotFoundError("Survey response")
    return row


async def save_section(
    *, token: str, section: int, changes: dict[str, Any]
) -> EducatorSurveyResponse:
    """Validate and persist one section's answers.

    Raises `ValidationError` naming the offending question, so the wizard can put the message beside
    the field rather than showing "invalid request" over a page of seventy-five inputs.
    """
    row = await resolve(token=token)
    if row.status != "partial":
        raise ConflictError(
            "This questionnaire has already been submitted",
            code="SURVEY_ALREADY_SUBMITTED",
        )
    if row.instrument_version != instrument.version():
        # A response opened against an earlier question bank cannot accept answers validated against
        # the current one: the same key may now be a different question. Better to say so than to
        # merge two vocabularies into one row.
        raise ConflictError(
            "This questionnaire has been updated. Please start again.",
            code="SURVEY_INSTRUMENT_CHANGED",
        )

    try:
        cleaned = instrument.validate_patch(changes)
    except instrument.AnswerError as exc:
        raise ValidationError(str(exc)) from exc

    # The consent answer is set once, at start, and is not something a later section may rewrite.
    cleaned.pop(instrument.CONSENT_QUESTION, None)

    contact = cleaned.pop(CONTACT_QUESTION, "__absent__")

    answers = dict(row.answers or {})
    for key, value in cleaned.items():
        if value is None:
            answers.pop(key, None)
        else:
            answers[key] = value

    updated = await educator_survey_repo.save_section(row.id, answers=answers, last_section=section)
    if updated is None:
        raise ConflictError(
            "This questionnaire is no longer open for changes",
            code="SURVEY_NOT_OPEN",
        )

    if contact != "__absent__":
        await educator_survey_repo.upsert_contact(row.id, contact)

    return updated


async def submit(*, token: str) -> dict[str, Any]:
    """Finish a response, or explain what is still needed.

    Returns a result rather than raising for the two expected shortfalls — a missing required answer
    and an under-filled "select exactly three" — because both are the form talking to the respondent.
    """
    row = await resolve(token=token)
    if row.status == "complete":
        # Idempotent: a double-tapped submit button, or a resumed link opened after finishing, should
        # see the same completed state rather than an error.
        return {
            "status": "complete",
            "submitted": False,
            "missing": [],
            "incomplete": [],
            "pruned": [],
        }

    answers = dict(row.answers or {})
    if not instrument.consent_given(answers):
        raise ConflictError("This questionnaire has no recorded consent", code="SURVEY_NO_CONSENT")

    pruned_answers, pruned = instrument.prune_hidden(answers)
    missing = instrument.missing_required(pruned_answers)
    incomplete = instrument.unmet_minimums(pruned_answers)
    if missing or incomplete:
        # Persist the pruning anyway: the respondent's edits are already reflected in what they see,
        # and losing them on a failed submit would ask them to redo work.
        if pruned:
            await educator_survey_repo.save_section(
                row.id, answers=pruned_answers, last_section=row.last_section
            )
        return {
            "status": "partial",
            "submitted": False,
            "missing": missing,
            "incomplete": incomplete,
            "pruned": pruned,
        }

    if not await educator_survey_repo.mark_complete(row.id, answers=pruned_answers):
        # Lost a race with another submit; the response is complete either way.
        return {
            "status": "complete",
            "submitted": False,
            "missing": [],
            "incomplete": [],
            "pruned": [],
        }

    if pruned:
        logger.info(
            "Educator survey %s: pruned %d answer(s) invalidated by later edits",
            row.id,
            len(pruned),
        )
    logger.info("Educator survey submitted: id=%s answers=%d", row.id, len(pruned_answers))
    return {
        "status": "complete",
        "submitted": True,
        "missing": [],
        "incomplete": [],
        "pruned": pruned,
    }
