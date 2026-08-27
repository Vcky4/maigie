"""Give the nightly goal ladder a memory, and name the reason it moves a deadline.

**A goal was allowed to become overdue and stay overdue.** Nothing ran over the goal table looking for
goals that had fallen behind: `is_at_risk`, `is_due_soon` and `is_overdue` were all written, all pure, and
all read only when a learner opened a page. So a goal drifted past its date and the only thing that
noticed was the label on a screen nobody was looking at.

This adds the two things a nightly pass needs before it can be trusted with the learner's deadlines.

**`GoalLifecycleAction` is the ladder's memory.** A ladder with no record extends the same deadline every
night and sends the same warning every morning — the behaviour that turns a helpful system into the one
the learner mutes. The cooldown is read from here, one query per candidate goal.

It cannot be read from the notification table instead, which is the obvious shortcut and a wrong one.
`create_notification` returns `None` under quiet hours or the daily cap, leaving no row, so a suppressed
warning would be indistinguishable from a warning never sent and the goal would be escalated again the
next night. That is the third time this programme has had to close that exact trap — the preparation ask
and the weekly check-in both record the *decision* rather than the message, and so does this.

It is deliberately separate from `GoalScheduleChange` even though extensions appear in both, because the
two answer different questions. `GoalScheduleChange` is the deadline's audit trail: every move, by anyone,
including a learner's own edits, and it is what the `extendedCount` field publishes. This is the ladder's
record of what *it* did, including the actions that move no date at all. An extension writes to both — a
deadline that moved must appear in the audit or `extendedCount` lies, and an action taken must appear here
or the cooldown lies.

**`system_extended` completes `GoalScheduleChange_reason_check`.** Migration 051 deliberately withheld it:
the ladder did not exist, and a token the schema offers that nothing can write is the accept-and-ignore
defect this codebase keeps closing. It arrives here with its writer. Widening a CHECK is safe in both
directions for existing rows, since no row can already hold a value the old constraint refused — which is
why the downgrade can restore the narrower constraint without inspecting anything.

**No response columns.** Whether the learner wants to deprioritise the goal, keep going, or says it is
already done is the most valuable thing this table could hold and nothing asks for it yet. The affordance
is phase 6 of the plan; the columns arrive with the question, on the same grounds as everything above.

**No backfill.** An empty table means the ladder has never acted, which is true, and it makes every
eligible goal immediately reviewable. Inventing history would put goals on a cooldown they never earned.

Revision ID: 053_goal_lifecycle
Revises: 052_plan_redistributed
Create Date: 2026-08-27

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the
DDL then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError`
about a value nobody wrote — see `046_schedule_block_completion`. This one is 18 characters.
"""

import sqlalchemy as sa

from alembic import op

revision = "053_goal_lifecycle"
down_revision = "052_plan_redistributed"
branch_labels = None
depends_on = None

TABLE = "GoalLifecycleAction"
CHANGES = "GoalScheduleChange"
REASON_CHECK = "GoalScheduleChange_reason_check"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("goalId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        # No `updatedAt`. A log entry describes a decision already taken and is never edited.
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # CASCADE on both, matching `GoalScheduleChange`. `delete_goal` is a hard DELETE and this
        # history has no reader once the goal is gone.
        sa.ForeignKeyConstraint(["goalId"], ["Goal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "action IN ('extended', 'asked_to_confirm', 'warned')",
            name="GoalLifecycleAction_action_check",
        ),
        sa.CheckConstraint(
            "trigger IN ('at_risk_due_soon', 'deadline_passed')",
            name="GoalLifecycleAction_trigger_check",
        ),
    )
    op.create_index("GoalLifecycleAction_goalId_createdAt_idx", TABLE, ["goalId", "createdAt"])
    op.create_index("GoalLifecycleAction_userId_idx", TABLE, ["userId"])

    # --- The reason the ladder moves a deadline, now that something does ---
    op.drop_constraint(REASON_CHECK, CHANGES, type_="check")
    op.create_check_constraint(
        REASON_CHECK,
        CHANGES,
        "reason IN ('learner_edited', 'plan_regenerated', 'system_extended')",
    )


def downgrade() -> None:
    # Narrowing the constraint again would be refused by any `system_extended` row the ladder has
    # already written, so those rows are relabelled rather than deleted. `plan_regenerated` is the
    # closest true statement available under the old vocabulary: a date the system moved, not the
    # learner. Deleting them would lose deadline moves that really happened and make `extendedCount`
    # under-report, which is the failure this table exists to prevent.
    op.execute(
        f'UPDATE "{CHANGES}" SET reason = \'plan_regenerated\' WHERE reason = \'system_extended\''
    )
    op.drop_constraint(REASON_CHECK, CHANGES, type_="check")
    op.create_check_constraint(
        REASON_CHECK,
        CHANGES,
        "reason IN ('learner_edited', 'plan_regenerated')",
    )

    op.drop_index("GoalLifecycleAction_userId_idx", table_name=TABLE)
    op.drop_index("GoalLifecycleAction_goalId_createdAt_idx", table_name=TABLE)
    op.drop_table(TABLE)
