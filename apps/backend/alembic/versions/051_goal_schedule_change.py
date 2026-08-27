"""Record every time a goal's deadline moves.

**An auto-extending goal marks itself healthy by moving its own goalposts.** `goal_metrics.elapsed_percent`
measures a goal's window as `createdAt → targetDate`. Push `targetDate` forward and the denominator grows,
elapsed percent falls, the lag `is_at_risk` tests against `AT_RISK_LAG_POINTS` shrinks, and the goal reports
itself on track. Nothing in the data distinguishes a goal that was always due in December from one that was
due in August and has been quietly rewritten twice — and the second learner is in trouble while their goal
says otherwise.

That matters now rather than later because the adaptive ladder this table is groundwork for *extends
deadlines on the learner's behalf*. Building it without a record would mean shipping a system whose main
intervention is invisible to the metrics that decide whether to intervene again.

**There is already one silent rewriter.** `regenerate_goal_plan` recomputes `targetDate` from a requested
duration and writes it, with no record and no regard for where the date came from — so an exam-derived
deadline can be moved by the plan regenerator today. This migration does not stop that; stopping it is a
behaviour change and belongs with the ladder. It makes it *visible*, which is the honest first step and the
one that cannot break anything.

**`dateAuthority` is snapshotted onto each row.** Everywhere else it is derived from `Goal.prepId`, and the
plan is explicit that it must stay derived rather than becoming a second field that can disagree with the
link. An entry in a log is the exception: `prepId` is `ON DELETE SET NULL`, so deleting a preparation would
retroactively reclassify every past change on its goal from `external` to `learner`, and what an entry is
*for* is what was true when the date moved. Same argument `050_prep_outcome` makes for copying readiness onto
the outcome instead of joining it back.

**No `system_extended` reason, yet.** The two tokens in the CHECK are the two paths that can move a date
today. The nightly ladder that would extend a deadline unprompted does not exist, and offering a value
nothing can write is the accept-and-ignore defect this codebase keeps closing — migration 032 removed a
column on the same grounds. It arrives with its writer. The learner's *response* to an extension
(`learnerResponse`, `respondedAt`, on the `RetentionIntervention` pattern) is absent for the same reason:
nothing asks yet.

**No backfill, and here there is not even a candidate.** Past date moves left no trace anywhere — no audit
column, no snapshot, nothing to reconstruct from. An empty table is the truthful starting state: it says
nothing is known about deadlines that moved before today, which is exactly right. Inventing a row per
existing goal from `createdAt` would assert a change that may never have happened.

Revision ID: 051_goal_sched_change
Revises: 050_prep_outcome
Create Date: 2026-08-27

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the DDL
then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError` about
a value nobody wrote — see `046_schedule_block_completion`. This one is 21 characters.
"""

import sqlalchemy as sa

from alembic import op

revision = "051_goal_sched_change"
down_revision = "050_prep_outcome"
branch_labels = None
depends_on = None

TABLE = "GoalScheduleChange"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("goalId", sa.String(), nullable=False),
        # Denormalised from the goal so "have this learner's deadlines been moving" stays one query.
        sa.Column("userId", sa.String(), nullable=False),
        # Both nullable. Null `previousDate` records a first deadline being set — a real schedule change,
        # but not an extension, which is why the published count requires both dates to be present.
        # Null `newDate` records a deadline being cleared.
        sa.Column("previousDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("dateAuthority", sa.String(), nullable=False),
        # No `updatedAt`. A log entry describes a moment that has already passed and is never edited —
        # the shape `ScheduleBehaviourLog` uses one table over.
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # CASCADE, matching `GoalMilestone`. This history exists to be shown beside a goal, so it has no
        # reader once the goal is deleted, and `SET NULL` would leave rows nothing can attribute.
        sa.ForeignKeyConstraint(["goalId"], ["Goal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "reason IN ('learner_edited', 'plan_regenerated')",
            name="GoalScheduleChange_reason_check",
        ),
        sa.CheckConstraint(
            "\"dateAuthority\" IN ('external', 'learner')",
            name="GoalScheduleChange_dateAuthority_check",
        ),
    )
    # Leads with `goalId`: the only read is one goal's history, and the count the goal response publishes
    # groups by it.
    op.create_index("GoalScheduleChange_goalId_createdAt_idx", TABLE, ["goalId", "createdAt"])
    op.create_index("GoalScheduleChange_userId_idx", TABLE, ["userId"])


def downgrade() -> None:
    op.drop_index("GoalScheduleChange_userId_idx", table_name=TABLE)
    op.drop_index("GoalScheduleChange_goalId_createdAt_idx", table_name=TABLE)
    op.drop_table(TABLE)
