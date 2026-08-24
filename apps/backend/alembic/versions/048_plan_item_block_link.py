"""Add StudyPlanItem.scheduleBlockId, so accepting a suggested time does not double the work.

The agenda composes the learner's day from four stores without materialising anything, and a
study-plan item appears on it as a *suggestion*: a day-scoped task with a proposed hour. When the
learner accepts one, `agenda_service.accept_placement` writes a real `ScheduleBlock`.

Nothing recorded that the two were the same commitment. The item stays `PENDING` — it has been
scheduled, not done — so the next read of the agenda returned both the new block and the same item,
still being suggested a time. One commitment, shown twice, with the second copy inviting the learner
to schedule something they had just scheduled.

`ReviewItem` already solved exactly this, with a nullable `scheduleBlockId` and a relationship that
lets `_read_topic_reviews` skip any item that already holds a block. This gives `StudyPlanItem` the
same link for the same reason, rather than inventing a second mechanism.

**Rejected: matching on `topicId` and the day.** A plan item's `topicId` is nullable and several items
in a plan can share one topic, so the match is both incomplete and ambiguous — it would hide an item
because a *different* item on the same topic had been scheduled.

**Rejected: a new `SCHEDULED` status on the item.** `status` is the learner's own lifecycle value and is
read by the study-plan screens; adding a value they do not recognise would drop the item out of their
plan view, which is a bigger change than recording a link.

The reader joins the block rather than trusting the id, so deleting the block puts the item back on the
agenda instead of losing it forever. That makes the link self-healing and means the delete path needs no
knowledge of study plans.

**No backfill.** Every existing item is `NULL`, which is correct: no block was ever created from one,
because until now there was no way to accept a suggestion at all.

Revision ID: 048_plan_item_block_link
Revises: 047_add_narrative_cache
Create Date: 2026-08-24

The id is 23 characters. `alembic_version.version_num` is `varchar(32)` in this database — see
migration 046, whose first attempt was 33 and failed on the version bump after the DDL had applied.
"""

import sqlalchemy as sa

from alembic import op

revision = "048_plan_item_block_link"
down_revision = "047_add_narrative_cache"
branch_labels = None
depends_on = None

TABLE = "StudyPlanItem"
COLUMN = "scheduleBlockId"
INDEX = "StudyPlanItem_scheduleBlockId_idx"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(), nullable=True))
    op.create_index(INDEX, TABLE, [COLUMN])
    # `SET NULL` rather than `CASCADE`: deleting a block un-schedules the item, it does not delete the
    # learner's plan item. The reader also verifies the block still exists, so the item reappears on the
    # agenda either way — but the constraint means the id cannot outlive the row it points at.
    op.create_foreign_key(
        "StudyPlanItem_scheduleBlockId_fkey",
        TABLE,
        "ScheduleBlock",
        [COLUMN],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("StudyPlanItem_scheduleBlockId_fkey", TABLE, type_="foreignkey")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
