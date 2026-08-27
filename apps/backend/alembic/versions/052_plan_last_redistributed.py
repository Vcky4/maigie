"""Let a background sweep repack a drifted study plan without churning it nightly.

**Rescheduling only fires when the learner is active, which inverts the need.**
`study_plan_service._redistribute_plan` has exactly two triggers: the learner editing the plan's schedule
inputs, and the learner marking an item complete while more than two pending items sit past due. So the
learners whose plans have drifted furthest — the ones who have stopped completing anything — are precisely
the ones who get no redistribution at all. A plan quietly accumulates a fortnight of overdue items and
nothing moves them.

This column is what lets a periodic pass close that gap without introducing a worse problem.
Redistribution re-anchors every pending item to tomorrow, so a nightly sweep with no memory would walk a
silent learner's whole remaining schedule forward by one day every night: dates that never settle, a diff
on every client poll, and a "plan" that is really just a rolling window. Stamped, the sweep can ask "has
this plan been repacked recently" and leave it alone if so.

Exactly the shape and the reason `lastCheckInAt` already has, one column up — a stored timestamp rather
than an assumption that the scheduler fired on time, so a retry inside the cooldown finds nothing to do
and a missed night is picked up the next one rather than compounding.

**Null rather than backfilled to now.** Every existing plan has never been swept, which is what null
means, and it is also what makes them immediately eligible. Backfilling to the deploy time would silently
put every drifted plan in the fleet on a cooldown it never earned, which is the opposite of the fix.

Nothing is stamped on the learner-driven paths. Completing an item still redistributes at once: that is a
response to something the learner just did, and throttling it would make the app feel broken rather than
restrained.

Revision ID: 052_plan_redistributed
Revises: 051_goal_sched_change
Create Date: 2026-08-27

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the
DDL then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError`
about a value nobody wrote — see `046_schedule_block_completion`. This one is 22 characters.
"""

import sqlalchemy as sa

from alembic import op

revision = "052_plan_redistributed"
down_revision = "051_goal_sched_change"
branch_labels = None
depends_on = None

TABLE = "StudyPlan"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("lastRedistributedAt", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(TABLE, "lastRedistributedAt")
