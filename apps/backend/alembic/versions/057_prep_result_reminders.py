"""A budget for asking about the mark, so the ask can exist at all.

**`resultValue` is what gates readiness calibration, and nothing has ever asked for it.**

The post-exam review collects how the exam *felt* — a 1–5 experience rating — immediately, which is right,
because a mark arrives weeks later and demanding one up front would collect neither. So `resultValue` is a
separate optional write, and both clients render a field for it. Nothing reminds anyone it is there. Measured:
one answered outcome, `resultValue` supplied only because it was requested by hand.

Calibrating a readiness *percentage* against a five-point self-report is not calibration. Phase 7's real gate
is outcomes carrying a mark, and without a reminder that population stays at whatever people happen to
volunteer — which is approximately nobody, weeks after they last thought about the exam.

## Two columns, the same shape the review ask already uses

`ExamPrep` has `reviewAskedAt` + `reviewRemindersSent` for exactly this job, and this mirrors it rather than
inventing a second pattern. The counter is a column and not a count of `Notification` rows for the reason
`GoalLifecycleAction` records: **a notification row is not evidence the learner was reached** — delivery can
be deferred by their daily allowance, held by quiet hours, or expire unread, and `create_notification` can
return `None` outright. What must be bounded is how many times *we asked*, which only a counter on the thing
being asked about can say.

Nullable and defaulted so the backfill is free: every existing outcome starts un-asked, which is true.

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, so it is kept short — see 055.
"""

import sqlalchemy as sa

from alembic import op

revision = "057_prep_result_reminders"
down_revision = "056_prep_completion_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "PrepOutcome",
        sa.Column("resultAskedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "PrepOutcome",
        sa.Column(
            "resultRemindersSent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Covers the sweep's whole predicate: unanswered marks, oldest answer first, within budget. Partial on
    # `resultValue IS NULL` because a recorded mark is permanently out of scope — the index only ever needs to
    # hold the rows still being chased, which shrinks as the feature works.
    op.create_index(
        "PrepOutcome_result_pending_idx",
        "PrepOutcome",
        ["answeredAt", "resultRemindersSent"],
        unique=False,
        postgresql_where=sa.text('"resultValue" IS NULL'),
    )


def downgrade() -> None:
    op.drop_index("PrepOutcome_result_pending_idx", table_name="PrepOutcome")
    op.drop_column("PrepOutcome", "resultRemindersSent")
    op.drop_column("PrepOutcome", "resultAskedAt")
