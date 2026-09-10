"""Educator research responses — SQLAlchemy models.

Two tables, and the split between them is a requirement of the research instrument rather than a
modelling preference. The questionnaire ends with: *"Store contact details separately from survey
responses where possible. Do not make contact information a requirement for completing the
questionnaire."* A nullable `contactDetail` column on the response row would satisfy the letter of
that and none of its purpose — every export, every admin list query and every analysis SELECT would
carry the identity of the respondent alongside their answers. A separate table means the analysis
surface can be read without ever joining to a person.

**Why answers are JSONB and not 75 columns.** The instrument is a versioned research document that
will be revised between runs. Seventy-five columns would make every wording change a migration, and a
revision that drops a question would either orphan a column forever or destroy the answers collected
under the previous version. `instrumentVersion` on the row is what makes an old response still
interpretable: it says which question bank the keys belong to.

**Why partial responses are first-class.** Seventy-five questions over twelve to fifteen minutes will
lose most respondents somewhere in the middle, and a respondent who stops at Q40 has still answered
the behavioural pain-point sections that carry most of the research value — the concept test is
downstream of them. So a row exists from the first saved section, `status` distinguishes `partial`
from `complete`, and nothing about the schema treats abandonment as a failed write.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base, TimestampMixin


class EducatorSurveyResponse(Base, TimestampMixin):
    """One educator's answers to the market-validation questionnaire."""

    __tablename__ = "EducatorSurveyResponse"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex[:25])

    # The resume credential, hashed for the same reason as `LandingDraft.tokenHash`: there is no
    # account behind it, so the token is the only thing standing between a stranger and a
    # part-finished set of answers. Returned once at creation and thereafter held only in the
    # respondent's browser and their resume link.
    token_hash: Mapped[str] = mapped_column(
        "tokenHash", String, nullable=False, unique=True, index=True
    )

    # Keyed `Q1`…`Q75`, with `Q13_other` alongside `Q13` for an "Other" elaboration. Validated against
    # the question bank on the way in, so an option that does not exist in the research document
    # cannot be stored.
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # Which question bank these keys belong to. Not a convenience: a revised instrument re-uses ids
    # like `Q40` for a different question, so a response without this is uninterpretable rather than
    # merely undated.
    instrument_version: Mapped[int] = mapped_column(
        "instrumentVersion", Integer, nullable=False, default=1, server_default="1"
    )

    # `partial` until the respondent submits. Both are data; see the module docstring.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="partial", server_default="partial"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        "submittedAt", DateTime(timezone=True), nullable=True
    )

    # The reviewer's workflow, kept distinct from the respondent's progress. A complete response can
    # still be unreviewed, and an archived one is still complete — one column could not say both.
    admin_status: Mapped[str] = mapped_column(
        "adminStatus", String, nullable=False, default="NEW", server_default="NEW"
    )

    # How far the respondent got, so the admin list can show progress without counting JSONB keys and
    # so a drop-off distribution is a single GROUP BY. Section number, not question number: the wizard
    # advances and autosaves by section.
    last_section: Mapped[int] = mapped_column(
        "lastSection", Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        # The admin list's default query: newest first within a review state.
        Index("EducatorSurveyResponse_adminStatus_createdAt_idx", "adminStatus", "createdAt"),
        # Segment counting ("how many complete?") and the partial-vs-complete filter.
        Index("EducatorSurveyResponse_status_idx", "status"),
        CheckConstraint(
            "status IN ('partial', 'complete')",
            name="EducatorSurveyResponse_status_check",
        ),
        CheckConstraint(
            "\"adminStatus\" IN ('NEW', 'REVIEWED', 'ARCHIVED')",
            name="EducatorSurveyResponse_adminStatus_check",
        ),
        # Complete means submitted at a knowable time. Without this, a bug that flipped the status
        # without stamping the time would leave a response that claims to be finished and cannot say
        # when — and every completion-rate figure computed from it would be quietly wrong.
        CheckConstraint(
            "(status <> 'complete') OR (\"submittedAt\" IS NOT NULL)",
            name="EducatorSurveyResponse_complete_stamp_check",
        ),
    )

    def __repr__(self) -> str:
        return f"<EducatorSurveyResponse id={self.id} status={self.status}>"


class EducatorSurveyContact(Base, TimestampMixin):
    """A respondent's follow-up contact detail, held apart from their answers.

    One row per response at most, created only when Q74 indicates willingness and Q75 supplies
    something to reach them by. `CASCADE`, not `SET NULL`: a contact detail with no response behind it
    is a bare address we have no stated purpose for, so it goes when the response goes.
    """

    __tablename__ = "EducatorSurveyContact"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex[:25])
    response_id: Mapped[str] = mapped_column(
        "responseId",
        String,
        ForeignKey("EducatorSurveyResponse.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Free text, not `EmailStr`: the instrument asks for "an email address or preferred contact
    # method", and a respondent who answers "WhatsApp, +234…" has answered the question asked.
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<EducatorSurveyContact response={self.response_id}>"
