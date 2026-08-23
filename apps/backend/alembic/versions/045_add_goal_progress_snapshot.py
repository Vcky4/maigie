"""Add GoalProgressSnapshot — the only history behind a goal's trajectory.

`ReflectGoalDetailPage` renders a progress trajectory and nothing records one. `Goal.progress` is
mutated in place, so yesterday's value is gone the instant it changes. `PrepReadinessSnapshot` reached
this conclusion for Prepare and `DailyLearningSnapshot` for Reflect; this is the same pattern, chosen
over inventing a third.

**No backfill, and this one genuinely cannot have one.** Migration `039`'s companion script
reconstructed ninety days of mastery from `Topic.completedAt`, because completion leaves a dated trail.
A goal's progress leaves none — it is a float overwritten by whoever last touched it, with no per-event
source to replay. Interpolating from `Goal.createdAt` to today's value was rejected explicitly: a
straight line presented as measurement is the defect this programme exists to close, and worse than an
empty chart because it looks finished. The table therefore starts empty and fills from the day the
nightly task first runs, with the chart saying so (Decision Y).

**The unique index is the writer's idempotency**, exactly as with the other two snapshot tables: a
retry, an overlapping run, or a second invocation on the same day updates the row rather than
duplicating the day. `(goalId, capturedOn)` rather than `(userId, goalId, capturedOn)` because
`goalId` already determines the learner.

`userId` is denormalised from `Goal` so a learner's whole history is one predicate and authorisation
needs no join — `DailyLearningSnapshot` carries it for the same reason. Both foreign keys are
`CASCADE`: a snapshot of a deleted goal, or of a deleted learner's goal, is not a record of anything.

`currentValue` is nullable and `currentValueMeasured` is not, which is the pair that keeps a measured
figure distinguishable from an asserted one after the fact — `metricKind` can be edited later, so a
reader cannot infer it from the goal as it stands today.

Revision ID: 045_add_goal_progress_snapshot
Revises: 044_add_goal_prep_fk
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "045_add_goal_progress_snapshot"
down_revision = "044_add_goal_prep_fk"
branch_labels = None
depends_on = None

TABLE = "GoalProgressSnapshot"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "goalId",
            sa.String(),
            sa.ForeignKey("Goal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capturedOn", sa.Date(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currentValue", sa.Float(), nullable=True),
        sa.Column(
            "currentValueMeasured",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(f"{TABLE}_goalId_idx", TABLE, ["goalId"])
    op.create_index(f"{TABLE}_userId_idx", TABLE, ["userId"])
    # The writer's idempotency, and the read path for one goal's series.
    op.create_index(
        f"{TABLE}_goalId_capturedOn_key", TABLE, ["goalId", "capturedOn"], unique=True
    )
    # A learner's whole history, for the portfolio-level reads.
    op.create_index(f"{TABLE}_userId_capturedOn_idx", TABLE, ["userId", "capturedOn"])


def downgrade() -> None:
    op.drop_index(f"{TABLE}_userId_capturedOn_idx", table_name=TABLE)
    op.drop_index(f"{TABLE}_goalId_capturedOn_key", table_name=TABLE)
    op.drop_index(f"{TABLE}_userId_idx", table_name=TABLE)
    op.drop_index(f"{TABLE}_goalId_idx", table_name=TABLE)
    op.drop_table(TABLE)
