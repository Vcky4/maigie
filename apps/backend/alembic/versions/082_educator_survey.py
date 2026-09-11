"""Educator market-validation survey: responses, and contacts kept apart from them.

The public site is gaining a 75-question research instrument for educators (M5 of the landing
programme). This is where the answers land.

**Why two tables.** The questionnaire's own closing instruction is: *"Store contact details separately
from survey responses where possible. Do not make contact information a requirement for completing the
questionnaire."* A nullable `contactDetail` column would meet the letter of that and defeat its
purpose — every list query, export and analysis SELECT would then carry a respondent's identity beside
their answers. With a second table, the analysis surface can be read without ever joining to a person,
and the one code path that writes an identity is findable by name.

**Why `answers` is JSONB.** Seventy-five columns would turn every wording revision into a migration,
and a revision that retires a question would either keep a dead column forever or destroy the answers
gathered under the previous version. `instrumentVersion` is what keeps an old row interpretable: a
revised instrument reuses ids like `Q40` for a different question, so without it the keys are
ambiguous rather than merely undated. The keys are `Q1`…`Q75`, with `Q13_other` beside `Q13` for an
"Other" elaboration, validated against the committed question bank on the way in.

**Why `status` distinguishes partial from complete.** Twelve to fifteen minutes of questions will lose
most respondents part-way, and someone who stops at Q40 has already answered the behavioural
pain-point sections that carry the research value — the concept test comes after them. Treating an
abandoned response as a failed write would discard most of what a long instrument is for, so a row
exists from the first saved section and abandonment is a state.

**Why `adminStatus` is a second column.** Review state and respondent progress answer different
questions: a complete response can be unreviewed, and an archived one is still complete. One column
could not say both.

Revision ID: 082_educator_survey
Revises: 081_landing_draft_email
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "082_educator_survey"
down_revision = "081_landing_draft_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "EducatorSurveyResponse",
        sa.Column("id", sa.String(), primary_key=True),
        # Hashed, not stored raw: there is no account behind this token, so it is the only thing
        # standing between a stranger and a part-finished response.
        sa.Column("tokenHash", sa.String(), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("instrumentVersion", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="partial"),
        sa.Column("submittedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adminStatus", sa.String(), nullable=False, server_default="NEW"),
        sa.Column("lastSection", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('partial', 'complete')",
            name="EducatorSurveyResponse_status_check",
        ),
        sa.CheckConstraint(
            "\"adminStatus\" IN ('NEW', 'REVIEWED', 'ARCHIVED')",
            name="EducatorSurveyResponse_adminStatus_check",
        ),
        # A response that claims to be complete must be able to say when. Without this, a bug that
        # set the status without the timestamp would make every completion-rate figure quietly wrong.
        sa.CheckConstraint(
            "(status <> 'complete') OR (\"submittedAt\" IS NOT NULL)",
            name="EducatorSurveyResponse_complete_stamp_check",
        ),
    )
    op.create_index(
        "EducatorSurveyResponse_tokenHash_key",
        "EducatorSurveyResponse",
        ["tokenHash"],
        unique=True,
    )
    op.create_index(
        "EducatorSurveyResponse_adminStatus_createdAt_idx",
        "EducatorSurveyResponse",
        ["adminStatus", "createdAt"],
    )
    op.create_index(
        "EducatorSurveyResponse_status_idx",
        "EducatorSurveyResponse",
        ["status"],
    )

    op.create_table(
        "EducatorSurveyContact",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("responseId", sa.String(), nullable=False),
        # Free text rather than an email column: the instrument asks for "an email address or
        # preferred contact method", so a respondent answering "WhatsApp, +234…" has answered it.
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # CASCADE, not SET NULL: a contact detail with no response behind it is a bare address with no
        # stated purpose, so it goes when the response goes.
        sa.ForeignKeyConstraint(["responseId"], ["EducatorSurveyResponse.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "EducatorSurveyContact_responseId_key",
        "EducatorSurveyContact",
        ["responseId"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("EducatorSurveyContact_responseId_key", table_name="EducatorSurveyContact")
    op.drop_table("EducatorSurveyContact")
    op.drop_index("EducatorSurveyResponse_status_idx", table_name="EducatorSurveyResponse")
    op.drop_index(
        "EducatorSurveyResponse_adminStatus_createdAt_idx", table_name="EducatorSurveyResponse"
    )
    op.drop_index("EducatorSurveyResponse_tokenHash_key", table_name="EducatorSurveyResponse")
    op.drop_table("EducatorSurveyResponse")
