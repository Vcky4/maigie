"""Landing drafts — Pydantic schemas.

Every bound here is deliberately tight. This is the only unauthenticated write surface in the
product, so the schema is the first and cheapest place to stop a caller who is not a visitor filling
in a wizard: three subjects and a sentence of goals is what the form can produce, and anything
larger is either a bug or someone using the endpoint as free storage.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from src.domains.personal_learning.models import LearningPurpose
from src.shared.schemas import CamelModel

#: Mirrors the DB CHECK constraint on `LandingDraft.status`.
DraftStatus = Literal["open", "claimed", "expired"]

#: What the wizard can send. Small on purpose — see the module docstring.
MAX_SUBJECTS = 5
MAX_SUBJECT_LENGTH = 120
MAX_GOALS_LENGTH = 500
MAX_EXAM_NAME_LENGTH = 200


def _clean_subjects(value: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, de-duplicate case-insensitively, and cap the count.

    Shared by create and update so the two cannot disagree about what a subject list is. The
    de-duplication is not politeness: `subjects[0]` becomes the primary subject of the generated
    preparation at claim time, and a list of `["Biology", "biology "]` would produce two courses on
    one subject.
    """
    if value is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in value:
        subject = (raw or "").strip()
        if not subject:
            continue
        if subject.lower() in seen:
            continue
        seen.add(subject.lower())
        cleaned.append(subject[:MAX_SUBJECT_LENGTH])
    return cleaned[:MAX_SUBJECTS]


class DraftCreateRequest(CamelModel):
    """Opening a draft. Everything is optional: the first chip click is enough to start one."""

    purpose: LearningPurpose | None = None


class DraftUpdateRequest(CamelModel):
    """Step 2 of the wizard. Fields left unset are left alone rather than cleared.

    `None` and "absent" have to mean different things here, because a visitor going back a step and
    clearing their exam date is a real edit. The route distinguishes them with
    `model_dump(exclude_unset=True)` rather than by treating null as no-op.
    """

    purpose: LearningPurpose | None = None
    subjects: list[str] | None = None
    goals: str | None = Field(None, max_length=MAX_GOALS_LENGTH)
    exam_name: str | None = Field(None, max_length=MAX_EXAM_NAME_LENGTH)
    exam_date: date | None = None

    @field_validator("subjects")
    @classmethod
    def _subjects(cls, value: list[str] | None) -> list[str] | None:
        return _clean_subjects(value)


class DraftPreviewItem(CamelModel):
    """One line of the sketch shown on step 3.

    Deliberately not a domain object. It is a label and a sentence, so a change to how preparations
    or plans are shaped cannot break a preview the visitor is already looking at, and an LLM
    returning something odd cannot smuggle a half-formed course into the response.
    """

    label: str = Field(max_length=80)
    detail: str = Field(max_length=280)


class DraftResponse(CamelModel):
    """A draft as the marketing site sees it.

    Note what is absent: `tokenHash`, and the token itself. The token is returned exactly once, by
    `DraftCreatedResponse`, and never again — a visitor who loses it has lost the draft, which is
    the correct outcome for a credential that authorises reading someone's data.
    """

    id: str
    status: DraftStatus
    purpose: str | None = None
    subjects: list[str] = Field(default_factory=list)
    goals_text: str | None = None
    exam_name: str | None = None
    exam_date: date | None = None
    preview: list[DraftPreviewItem] = Field(default_factory=list)
    can_generate: bool = True
    expires_at: datetime


class DraftCreatedResponse(DraftResponse):
    """The create response, and the only place the raw token appears."""

    token: str


class ClaimRequest(CamelModel):
    """Handing a draft to the account that just signed up."""

    token: str = Field(min_length=8, max_length=256)


class ClaimResponse(CamelModel):
    """The outcome of a claim.

    `applied=False` with a `reason` rather than an error status for the expected misses (expired,
    already claimed, unknown token, profile already set up). A visitor must never be blocked from
    their new account because a marketing draft went stale, so the client treats every one of these
    as "carry on with normal onboarding" — see §4.4 of the landing plan.
    """

    applied: bool
    reason: str | None = None
    purpose: str | None = None
    subjects: list[str] = Field(default_factory=list)
