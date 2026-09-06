"""Add DailyLearningSnapshot — the only history behind Reflect's trends.

Every trend the Reflect surface renders is a question about the past: the growth curve over
7/30/90 days, per-subject `change`, `masteryChange`, `consistencyChange`, and the library's
monthly rhythm. None of them are answerable from the tables that exist, because the values
they trend are **mutable in place**. `Course.progress` is overwritten by
`recount_course_progress`; `LearningProfile.consistencyScore` is overwritten by the nightly
behaviour task. The previous value is not archived anywhere — it is gone. No query recovers it.

This is the same conclusion `011_add_readiness_snapshots` reached for the Prepare surface, and
this migration deliberately copies that shape rather than inventing a second way to store a
daily figure.

**Why a daily row and not an event log.** The surface asks "what was mastery on 12 July", not
"what changed at 14:22". A row per learner per day answers the question the design actually
asks, at a fraction of the volume, and `ActivityFeedEntry` already covers the event-level need.

**`snapshotDate` is the learner's local day, not a UTC day.** Written from `to_learner_local`.
A session at 23:30 in Lagos is the next day in UTC, so bucketing by UTC date either merges two
of the learner's days or splits one across two — and `activeDay` and the rhythm chart are
questions about the learner's own calendar. Note that `011`'s writer truncates to a UTC date
and is recorded as having that bug; this table is not repeating it.

`uniqueConstraint(userId, snapshotDate)` is what makes the writer idempotent: a retry, a
re-run, a manual backfill over a day already recorded, or two workers on the same night update
the row rather than duplicating the day.

**Percentages are nullable, deliberately.** A learner who reviewed no cards has no recall
percentage, and `0.0` there would draw a line at the bottom of a chart asserting total failure
to remember. Not-measured is not zero, and the column type is where that gets enforced rather
than in every reader. The counters (`sessionsCompleted`, `cardsReviewed`, `topicsCompleted`)
are NOT NULL with `0` defaults, because for those zero is a real measurement.

**`reconstructed`** marks rows the 90-day backfill produced rather than rows recorded on the
day. Only `overallMasteryPercent` and `subjectMastery` are approximate in those rows — the
denominator is today's topic count, and a topic completed then later reopened has lost its
`completedAt` — and both distortions understate, so a reconstructed trend never invents growth
that did not happen. The flag is published per point so the client can footnote it. A learner
told "mastery before this date is estimated from your completion history" has been told the
truth; a learner shown a seamless curve has not.

No backfill in this migration. The table is created empty and populated by
`scripts/backfill_daily_snapshots.py`, because a 90-day reconstruction for every learner is a
long-running job that must be re-runnable and observable, which a migration is neither.

Revision ID: 039_add_daily_snapshot
Revises: 038_reflection_contract
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "039_add_daily_snapshot"
down_revision = "038_reflection_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "DailyLearningSnapshot",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # A date, not a timestamp: the unit is a day, and that is what makes the writer
        # idempotent through the unique constraint below.
        sa.Column("snapshotDate", sa.Date(), nullable=False),
        # --- Effort and attendance ---
        # Null when nothing reported a duration. Absent time means untracked far more often
        # than it means idle, so it must not arrive as zero.
        sa.Column("focusedMinutes", sa.Float(), nullable=True),
        sa.Column("sessionsCompleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activeDay", sa.Boolean(), nullable=False, server_default="false"),
        # --- Outcome ---
        # Copied from LearningProfile rather than recomputed: one definition, one writer.
        sa.Column("consistencyScore", sa.Float(), nullable=True),
        sa.Column("overallMasteryPercent", sa.Float(), nullable=True),
        sa.Column("cardsReviewed", sa.Integer(), nullable=False, server_default="0"),
        # Null until at least one card has been reviewed.
        sa.Column("recallPercent", sa.Float(), nullable=True),
        sa.Column("topicsCompleted", sa.Integer(), nullable=False, server_default="0"),
        # Absolute with fixed caps, not normalised against a personal maximum — see the
        # snapshot service. Null on a day with no snapshot-worthy work.
        sa.Column("effortScore", sa.Float(), nullable=True),
        # {courseId: masteryPercent}. Read whole for a date range and never queried by
        # course, so a row-per-subject-per-day child table would multiply the row count to
        # serve no query that exists.
        sa.Column("subjectMastery", sa.JSON(), nullable=True),
        sa.Column("reconstructed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("userId", "snapshotDate", name="DailyLearningSnapshot_unique"),
    )
    # The trend query: one learner, a bounded range of days, oldest first.
    op.create_index(
        "DailyLearningSnapshot_userId_snapshotDate_idx",
        "DailyLearningSnapshot",
        ["userId", "snapshotDate"],
    )


def downgrade() -> None:
    op.drop_index(
        "DailyLearningSnapshot_userId_snapshotDate_idx", table_name="DailyLearningSnapshot"
    )
    op.drop_table("DailyLearningSnapshot")
