"""Add daily readiness snapshots so a trend becomes expressible.

A readiness trend cannot be derived from what exists. Topic mastery is a single
mutable float per topic: when it changes, the previous value is gone. There is no
history anywhere in the domain to reconstruct, so the workspace's trend chart and
Analytics tab had nothing behind them and could not be given anything without new
storage.

One row per preparation per day, written by a Celery beat task from the same
`prep_readiness` helper that serves live reads, so a snapshot can never disagree
with what the dashboard showed that day.

``uniqueConstraint(prepId, capturedOn)`` makes the writer idempotent: a re-run,
a retry, or two workers on the same day update the row rather than duplicating it.

Percentages are nullable to preserve the not-measured-versus-zero rule used
throughout. A preparation with no topics has no measurable readiness, and
recording it as `0` would draw a chart flatlining at the bottom instead of
showing no line at all.

Revision ID: 011_add_readiness_snapshots
Revises: 010_add_question_flags
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "011_add_readiness_snapshots"
down_revision = "010_add_question_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PrepReadinessSnapshot",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # A date, not a timestamp: the unit is a day, and that is what makes the
        # writer idempotent.
        sa.Column("capturedOn", sa.Date(), nullable=False),
        sa.Column("progressPercent", sa.Float(), nullable=False),
        # Null when there are no topics to average.
        sa.Column("averageMasteryPercent", sa.Float(), nullable=True),
        sa.Column("topicsTotal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topicsStrong", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topicsFocus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topicsAssessed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questionsAnswered", sa.Integer(), nullable=False, server_default="0"),
        # Null until at least one question has been answered.
        sa.Column("accuracyPercent", sa.Float(), nullable=True),
        sa.Column("quizzesTaken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("prepId", "capturedOn", name="PrepReadinessSnapshot_unique"),
    )
    # The trend query: one preparation, most recent days first.
    op.create_index(
        "PrepReadinessSnapshot_prepId_capturedOn_idx",
        "PrepReadinessSnapshot",
        ["prepId", "capturedOn"],
    )


def downgrade() -> None:
    op.drop_index("PrepReadinessSnapshot_prepId_capturedOn_idx", table_name="PrepReadinessSnapshot")
    op.drop_table("PrepReadinessSnapshot")
