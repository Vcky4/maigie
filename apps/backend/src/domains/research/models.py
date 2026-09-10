"""Schemas for the educator research surface.

Answers are deliberately typed as a loose mapping here and validated against the question bank in
`instrument.py`. Restating seventy-five questions as Pydantic fields would duplicate the research
document in a second place, and the copy that drifts is always the one further from the source.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from src.shared.schemas import CamelModel

SurveyStatus = Literal["partial", "complete"]
AdminStatus = Literal["NEW", "REVIEWED", "ARCHIVED"]

#: A cap on how many answers one request may carry. The largest legitimate section is eleven
#: questions; this leaves room for their `_other` siblings and still bounds a hostile payload.
MAX_ANSWERS_PER_PATCH = 40


class SurveyStartRequest(CamelModel):
    """Begin a response. Consent is the only thing that can start one."""

    #: Must be the affirmative option from Q1. Sent explicitly rather than inferred, so the row cannot
    #: come into existence without a recorded yes.
    consent: str = Field(min_length=1, max_length=120)
    #: A field no human fills in. Present rather than absent, so a bot that submits every input it
    #: finds identifies itself.
    honeypot: str | None = Field(None, max_length=200)


class SurveyStartedResponse(CamelModel):
    """The only response carrying the raw resume token."""

    id: str
    token: str
    instrument_version: int


class SurveySectionRequest(CamelModel):
    """One section's answers, as `{questionId: value}` plus optional `Q13_other` siblings.

    An explicit `null` clears an answer, which is how a respondent unselecting an option reaches the
    server. Absent keys are left untouched, so a section save never has to resend the whole response.
    """

    section: int = Field(ge=0, le=11)
    answers: dict[str, Any] = Field(default_factory=dict, max_length=MAX_ANSWERS_PER_PATCH)


class SurveyResponseState(CamelModel):
    """What the wizard needs to resume, and nothing more.

    Carries no contact detail even when one is stored: it lives in its own table for the reason in
    `db_models`, and a resume payload has no use for it.
    """

    id: str
    status: SurveyStatus
    instrument_version: int
    last_section: int
    answers: dict[str, Any] = Field(default_factory=dict)
    submitted_at: datetime | None = None


class SurveySubmitResponse(CamelModel):
    """The outcome of a submit attempt.

    `missing` and `incomplete` are returned rather than raised as a 422, because a respondent who has
    left the one required question blank is having a conversation with the form, not making a bad
    request.
    """

    status: SurveyStatus
    submitted: bool
    missing: list[str] = Field(default_factory=list)
    incomplete: list[str] = Field(default_factory=list)
    #: Answers dropped because the respondent's own later edits made those questions inapplicable.
    pruned: list[str] = Field(default_factory=list)


class AdminSurveyListItem(CamelModel):
    """One row of the admin list.

    The five research-relevant columns the plan asks for (role, organisation, concept relevance,
    next-step interest) are projected out of the answers here rather than left for the client to dig
    out of a JSONB blob, so the list can be scanned without opening each response.
    """

    id: str
    status: SurveyStatus
    admin_status: AdminStatus
    instrument_version: int
    last_section: int
    created_at: datetime
    submitted_at: datetime | None = None
    roles: list[str] = Field(default_factory=list)
    organisation: str | None = None
    concept_relevance: int | None = None
    next_step_interest: str | None = None
    has_contact: bool = False


class AdminSurveyListResponse(CamelModel):
    items: list[AdminSurveyListItem]
    total: int
    limit: int
    offset: int


class AdminSurveyDetail(CamelModel):
    """A full response for review, with the contact detail attached explicitly.

    Contact arrives as its own field rather than inside `answers` — the same separation the storage
    keeps, carried through to the API so a detail view is the only surface where the two meet.
    """

    id: str
    status: SurveyStatus
    admin_status: AdminStatus
    instrument_version: int
    last_section: int
    created_at: datetime
    updated_at: datetime | None = None
    submitted_at: datetime | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    contact_detail: str | None = None


class AdminStatusUpdateRequest(CamelModel):
    admin_status: AdminStatus
