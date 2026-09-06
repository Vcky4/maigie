"""Add ScheduleBlock.completedAt, so a planned session can be recorded as done.

Both goal pages draw "planned versus completed actions". Planned was always answerable — a `COUNT`
over `ScheduleBlock.goalId` — and **completed was recorded nowhere**: the row has no flag, no status
and no completion timestamp, and `regenerate_goal_plan` creates blocks without any notion of one.

Two alternatives were considered and rejected.

*Infer it from a `StudySession` overlapping the block's window.* There is no `scheduleBlockId` on
`StudySession`, so this is a time coincidence wearing the word "completed" — it would credit a learner
who sat down at the right hour and studied something else, and silently report zero for the many
learners nothing writes a `StudySession` for at all.

*Read `ScheduleBehaviourLog`.* It has exactly the right planned-versus-actual shape, with `scheduledAt`
and `actualAt` columns — and nothing in the application has ever written a row to it. Building the chart
on it would have produced an endpoint that returned zeros forever and looked like a learner who never
follows their plan.

So completion becomes recordable, and the series reads zero completed until learners start using it —
the same shape as Decision Y's goal history: a truthful empty beats a plausible invention.

**A nullable timestamp, not a boolean.** A Tuesday session marked done on Thursday keeps Tuesday's own
date, un-completing is expressible by setting it back to null, and "when" is a question the momentum
chart will want later. `GoalMilestone.achievedAt` already made this choice for the same reasons.

**No backfill.** Every existing block is `NULL`, which is correct: nobody ever told us any of them
happened. Marking historical blocks complete because their date has passed would assert attendance
nobody recorded.

Also adds `(goalId, startAt)`, which is the momentum read's index — every block for one goal, bucketed
by the week it was planned for. The existing indexes are `(userId, startAt)` and `(startAt, endAt)`, so
that query would otherwise scan the learner's whole schedule to find one goal's blocks.

Revision ID: 046_schedule_block_completion
Revises: 045_add_goal_progress_snapshot
Create Date: 2026-08-23

Note on the revision id: `alembic_version.version_num` in this database is `varchar(32)`, and the
first attempt at this migration was called `046_add_schedule_block_completion` — 33 characters. The
DDL applied and the version bump then failed on the way out, so the whole transaction rolled back and
the only symptom was a `StringDataRightTruncationError` about a value nobody had written. The longest
id already in this tree is exactly 32, so the ceiling had never been hit before. Keep new ids short.
"""

import sqlalchemy as sa

from alembic import op

revision = "046_schedule_block_completion"
down_revision = "045_add_goal_progress_snapshot"
branch_labels = None
depends_on = None

TABLE = "ScheduleBlock"
INDEX = "ScheduleBlock_goalId_startAt_idx"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True))
    op.create_index(INDEX, TABLE, ["goalId", "startAt"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "completedAt")
