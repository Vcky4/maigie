"""Add ScheduleBlock.startedAt so a study session's start is observable.

A study-session reminder's meaningful outcome is that the learner starts the session, but the block
recorded only `completedAt` — so a learner who studied the block and never ticked "done" looked, to
the outcome funnel, like a reminder that failed. This adds the start timestamp the completion
column's own docstring already anticipated ("start, complete, snooze, or reschedule").

Nullable with no default and no backfill: `None` means not started, and every existing row is
correctly not-started under that reading. The down-revision drops it (safe — nothing depends on it
before this revision).
"""

import sqlalchemy as sa

from alembic import op

revision = "074_schedule_block_started_at"
down_revision = "073_notification_system_surface"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ScheduleBlock",
        sa.Column("startedAt", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ScheduleBlock", "startedAt")
