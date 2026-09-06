"""Add ReflectionNote — the learner's own writing, stored.

The `/reflections` page has a quick-note box with three starter prompts, and until now it had
nowhere to send what the learner typed. Its own label said so: "saved locally for now", which is
an honest admission and still means a learner who wrote something reflective and refreshed lost
it.

**A separate table, not a `Reflection` row with a null narrative.** `Reflection` is written by a
weekly Celery task, is unique per `(userId, type, periodStart)`, and is the table the library
page counts and `GET /reflections?type=weekly` filters. Storing learner prose there would put
journal entries into a list of generated period summaries, count them as though the scheduler
produced them, and make a note compete for a uniqueness slot describing a period it does not
have. A generated reflection and a written note are different kinds of thing that happen to both
be reflective.

`promptUsed` records which starter prompt seeded a note, and is nullable because a learner can
write unprompted. It stores the prompt *text* rather than an id, because the prompt list is copy
that will be reworded and a note should keep the words the learner was actually answering. It is
also the only way to ever answer whether offering the prompts helps.

Revision ID: 040_add_reflection_notes
Revises: 039_add_daily_snapshot
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "040_add_reflection_notes"
down_revision = "039_add_daily_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ReflectionNote",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        # Null when the learner wrote unprompted.
        sa.Column("promptUsed", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # The list query: one learner's notes, newest first. No separate index on `userId` alone —
    # this one leads with it, so it serves those lookups and the FK cascade too.
    op.create_index(
        "ReflectionNote_userId_createdAt_idx", "ReflectionNote", ["userId", "createdAt"]
    )


def downgrade() -> None:
    op.drop_index("ReflectionNote_userId_createdAt_idx", table_name="ReflectionNote")
    op.drop_table("ReflectionNote")
