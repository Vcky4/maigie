"""Add PrepOutcome, and the columns that track asking for it.

**A preparation was being completed by a clock.** `exam_prep_service.mark_overdue_preparations_completed`
runs nightly, selects every `ExamPrep` whose `examDate` has passed, and sets `status = COMPLETED`. So a
learner who was 30 percent ready for an exam they missed got a preparation recorded as finished, and the
preparation then dropped out of `PREP_STATUSES_WORTH_A_GOAL` so it was not even a candidate for a goal
any more. The date passing says the exam happened — not that they sat it, not that they were ready, and
not that it went well. The only party who knows is the learner, and nothing had ever asked: `ExamPrep`
carried no outcome, score, rating or reflection column at all.

This adds the answer, and the state between "in progress" and "finished" that the sweep was skipping.

**A table rather than columns on `ExamPrep`.** A postponed exam is a second sitting of the same
preparation and produces a second outcome; columns would overwrite the first. `examDate` is copied onto
the row so it records *which* sitting the answer is about, and `(prepId, examDate)` is unique so a
retried submit updates rather than recording the exam twice.

**The readiness figures are copied onto the row.** They are recoverable from `PrepReadinessSnapshot`
today, but copying them makes the calibration question — did our readiness figure predict anything? — a
single-table query, and survives the snapshot being pruned. This is the point of the whole change:
`progress_percent` is a prediction that has been shown to learners and used to gate goal progress
without ever once being compared against an outcome, because no outcome existed.

**No backfill, and the reason is the same one migration 046 gives.** Every preparation already marked
`COMPLETED` by the old sweep keeps that status. Rewriting them to `AWAITING_REVIEW` would start asking
learners about exams they sat months ago, and marking them unreviewed would assert we know the old
completions were wrong when what we know is that they were unverified. Historical rows are left as they
are; the new path applies from here. `reviewRemindersSent` defaults to 0 server-side, which is correct
for existing rows: nobody has been asked.

Revision ID: 050_prep_outcome
Revises: 049_chat_msg_grounding
Create Date: 2026-08-26

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the
DDL then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError`
about a value nobody wrote — see `046_schedule_block_completion`. This one is 16 characters.

Numbered 050 rather than 049 because `049_chat_msg_grounding` already claims 048 as its parent, and two
revisions with one parent is a branch alembic will refuse to upgrade past without a merge.
"""

import sqlalchemy as sa

from alembic import op

revision = "050_prep_outcome"
down_revision = "049_chat_msg_grounding"
branch_labels = None
depends_on = None

PREP = "ExamPrep"
TABLE = "PrepOutcome"


def upgrade() -> None:
    # --- The ask, tracked on the preparation ---
    #
    # These belong on `ExamPrep` rather than on the outcome, because they describe a question that has
    # been put and not yet answered — at which point no outcome row exists to hold them.
    op.add_column(PREP, sa.Column("reviewAskedAt", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        PREP,
        sa.Column(
            "reviewRemindersSent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(PREP, sa.Column("reviewDeclinedAt", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("prepId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("examDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attended", sa.String(16), nullable=False),
        sa.Column("experienceRating", sa.Integer(), nullable=True),
        sa.Column("preparationRating", sa.Integer(), nullable=True),
        sa.Column("reflection", sa.Text(), nullable=True),
        sa.Column("resultValue", sa.Float(), nullable=True),
        sa.Column("resultScale", sa.String(), nullable=True),
        sa.Column("resultRecordedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answeredAt", sa.DateTime(timezone=True), nullable=False),
        # What was believed on the day. Nullable throughout: a preparation with no topics has nothing
        # to measure, and `0` there would claim a measured absence of readiness.
        sa.Column("readinessPercent", sa.Float(), nullable=True),
        sa.Column("averageMasteryPercent", sa.Float(), nullable=True),
        sa.Column("topicsTotal", sa.Integer(), nullable=True),
        sa.Column("topicsStrong", sa.Integer(), nullable=True),
        sa.Column("targetReadiness", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False),
        # `CASCADE` on the preparation: an outcome is part of that preparation's record and means
        # nothing without it. Same on the learner.
        sa.ForeignKeyConstraint(["prepId"], [f"{PREP}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("prepId", "examDate", name="PrepOutcome_prepId_examDate_key"),
        # Closed sets in the database as well as in Pydantic. `Reflection.type` is the precedent: an
        # unconstrained String let a task write "WEEKLY" while the service branched on "weekly".
        sa.CheckConstraint(
            "attended IN ('sat', 'missed', 'postponed', 'cancelled')",
            name="PrepOutcome_attended_check",
        ),
        sa.CheckConstraint(
            '"experienceRating" IS NULL OR ("experienceRating" BETWEEN 1 AND 5)',
            name="PrepOutcome_experienceRating_check",
        ),
        sa.CheckConstraint(
            '"preparationRating" IS NULL OR ("preparationRating" BETWEEN 1 AND 5)',
            name="PrepOutcome_preparationRating_check",
        ),
    )
    op.create_index("PrepOutcome_prepId_idx", TABLE, ["prepId"])
    op.create_index("PrepOutcome_userId_answeredAt_idx", TABLE, ["userId", "answeredAt"])


def downgrade() -> None:
    op.drop_index("PrepOutcome_userId_answeredAt_idx", table_name=TABLE)
    op.drop_index("PrepOutcome_prepId_idx", table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_column(PREP, "reviewDeclinedAt")
    op.drop_column(PREP, "reviewRemindersSent")
    op.drop_column(PREP, "reviewAskedAt")
