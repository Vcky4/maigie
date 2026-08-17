"""Record which phase writing a lesson has reached, so progress can be shown.

Writing a lesson is the largest generation in the product — up to twelve sections with paragraphs,
steps and code, plus objectives and a knowledge check, at an 8192-token budget, twice the outline's.
The reader showed a pulsing icon and one sentence for the whole of it, and that sentence claimed the
lesson would open "one section at a time", which was never true: the whole thing lands in one reply.

Migration `019` did this for quizzes and recorded the principle it followed: every stage shown must
have a server-side write behind it, because a bar driven by a timer reports state the browser has no
access to — it would say "writing" for a request that had already failed parsing. This is the same
column for the same reason.

## Why one column and not the quiz's full treatment

Quiz generation was **backgrounded** as well as staged, because a synchronous 16s POST was measured
past Decision H's 10s threshold. Lesson generation stays in its request, and the client observes these
stages through a *concurrent* read of `GET /knowledge/topics/{id}` while its own POST is still open.
The stages are real writes either way; what differs is the terminal signal. For a quiz it is
`status`, polled. For a lesson it is the learner's own request resolving, which is strictly more
reliable — there is no lost-task case to bound, because the client holding the request is the client
watching the stage.

The cost of that choice is that a generation abandoned by a server restart leaves this column set. It
is nullable and self-correcting: the next open starts a fresh generation and overwrites it, and every
terminal path clears it. Nothing derives anything from a stale value.

Nullable, not backfilled. A topic whose lesson was written before this column existed has no stage,
and writing `READY` onto it would claim a history nobody observed.

Revision ID: 030_add_lesson_stage
Revises: 029_add_check_attempts
Create Date: 2026-08-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "030_add_lesson_stage"
down_revision = "029_add_check_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Topic", sa.Column("lessonGenerationStage", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("Topic", "lessonGenerationStage")
