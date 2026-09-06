"""Persist a learner's flag on a question for later review.

The practice fixture let a learner flag a question and then forgot it as soon as
the component unmounted. Flagging is only worth anything if it survives: the
point is to come back to it.

Scoped by ``(userId, prepQuestionId)``, **not** by session. A learner flags a
*question*, so it should still be flagged the next time they meet it — which is
only expressible now that questions outlive the session that asked them
(migration 008).

The unique constraint makes flagging idempotent, matching the decision that
answering is idempotent: pressing the button twice is not an error.

Revision ID: 010_add_question_flags
Revises: 009_add_question_metadata
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "010_add_question_flags"
down_revision = "009_add_question_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PrepQuestionFlag",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prepQuestionId",
            sa.String(),
            sa.ForeignKey("PrepQuestion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Why the learner flagged it. Optional: the act of flagging is the signal,
        # and demanding a reason would suppress it.
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("userId", "prepQuestionId", name="PrepQuestionFlag_unique"),
    )
    op.create_index("PrepQuestionFlag_userId_idx", "PrepQuestionFlag", ["userId"])
    op.create_index("PrepQuestionFlag_prepQuestionId_idx", "PrepQuestionFlag", ["prepQuestionId"])


def downgrade() -> None:
    op.drop_index("PrepQuestionFlag_prepQuestionId_idx", table_name="PrepQuestionFlag")
    op.drop_index("PrepQuestionFlag_userId_idx", table_name="PrepQuestionFlag")
    op.drop_table("PrepQuestionFlag")
