"""Record when a topic was completed.

`Topic.completed` is a boolean and nothing else. So a finished topic carries no time, and every
question the course library asks about recent work has had no answer:

- "Recently active", and the per-course "last studied" beside it.
- A study streak.
- Anything "this week".

The two things that look like they could stand in cannot. `Topic.updatedAt` moves whenever the row
is touched — renaming a topic or generating content into it — so reading it as study activity would
report an edit as learning. And `UserTopicProgress`, which does have `completedAt` and
`minutesSpent`, is **written by nothing**: it was added for shared circle courses and no code path
populates it, so it is an empty table, not a source.

`completedAt` on the topic is the smaller and more honest of the two fixes. It mirrors
`StudyPlanItem.completedAt`, which the study-plan surfaces already derive their streak and activity
feed from, so both areas answer "when" the same way.

**Minutes are still not recorded, deliberately.** Nothing anywhere observes how long a learner spent
on a topic, and adding a column for it would not make the measurement exist. Effort is reported the
way study plans report it: the estimated hours of the topics that got completed, labelled as planned
rather than measured. Inventing a timer is a product decision, not a migration.

Nullable, and not backfilled. A topic completed before this column existed has no completion time,
and choosing one — `updatedAt`, or the migration's own timestamp — would put a date on screen that
nothing observed. Those topics are absent from the activity feed and count towards progress, which
is exactly what is true of them.

Revision ID: 024_add_topic_completed
Revises: 023_add_plan_rhythm
Create Date: 2026-08-15

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls it back.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "024_add_topic_completed"
down_revision = "023_add_plan_rhythm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Topic", sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True))
    # The activity feed and the streak both read "this learner's recent completions", which is a scan
    # over completed topics ordered by time. Partial, because the rows with a null here are the ones
    # those queries never want.
    op.create_index(
        "Topic_completedAt_idx",
        "Topic",
        ["completedAt"],
        postgresql_where=sa.text('"completedAt" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index("Topic_completedAt_idx", table_name="Topic")
    op.drop_column("Topic", "completedAt")
