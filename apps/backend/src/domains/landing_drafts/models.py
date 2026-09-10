"""Tightly bounded schemas for the unauthenticated landing-draft surface.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import EmailStr, Field, field_validator

from src.domains.personal_learning.models import LearningPurpose
from src.shared.schemas import CamelModel

DraftStatus = Literal["open", "claimed", "expired"]
MAX_SUBJECTS = 5
MAX_SUBJECT_LENGTH = 120
MAX_GOALS_LENGTH = 500
MAX_EXAM_NAME_LENGTH = 200


def _clean_subjects(value: list[str] | None) -> list[str] | None:
    """Trim, remove blanks, de-duplicate case-insensitively, and cap subjects."""
    if value is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in value:
        subject = (raw or "").strip()
        key = subject.lower()
        if not subject or key in seen:
            continue
        seen.add(key)
        cleaned.append(subject[:MAX_SUBJECT_LENGTH])
    return cleaned[:MAX_SUBJECTS]


class DraftCreateRequest(CamelModel):
    """Opening a draft; the first goal selection is enough."""

    purpose: LearningPurpose | None = None


class DraftUpdateRequest(CamelModel):
    """Wizard answers. Omitted fields remain unchanged; explicit null clears a field."""

    purpose: LearningPurpose | None = None
    subjects: list[str] | None = None
    goals: str | None = Field(None, max_length=MAX_GOALS_LENGTH)
    exam_name: str | None = Field(None, max_length=MAX_EXAM_NAME_LENGTH)
    exam_date: date | None = None
    #: Validated as an address rather than stored as free text, because it is used to contact a real
    #: person and to prefill signup. A malformed value is a client bug, not data to keep.
    email: EmailStr | None = None

    @field_validator("subjects")
    @classmethod
    def _subjects(cls, value: list[str] | None) -> list[str] | None:
        return _clean_subjects(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None


class DraftResponse(CamelModel):
    """The deterministic values needed to prefill onboarding.

    The token hash and raw token are absent. The raw token is returned only when the draft is
    created, then acts as the opaque handoff id for subsequent reads and the final claim.
    """

    id: str
    status: DraftStatus
    email: str | None = None
    purpose: str | None = None
    subjects: list[str] = Field(default_factory=list)
    goals_text: str | None = None
    exam_name: str | None = None
    exam_date: date | None = None
    expires_at: datetime


class DraftCreatedResponse(DraftResponse):
    """Create response and the only response containing the raw token."""

    token: str


class ClaimRequest(CamelModel):
    """Hand a temporary draft to an authenticated account."""

    token: str = Field(min_length=8, max_length=256)


class ClaimResponse(CamelModel):
    """Ownership outcome plus the saved values the authenticated client must replay."""

    applied: bool
    reason: str | None = None
    purpose: LearningPurpose | None = None
    subjects: list[str] = Field(default_factory=list)
    goals_text: str | None = None
    exam_name: str | None = None
    exam_date: date | None = None
