"""Record which phase of generation a quiz session is in, so progress can be shown.

Decision H fixed quiz start as synchronous "until p95 start latency exceeds 10s".
Migration `018` made that measurable and the first reading settled it: **p50 16,346ms,
max 17,738ms** across six sessions, every sample well past the threshold. Phase 4e had
already deferred a real staged progress bar to exactly this reading, and refused to fake
one in the meantime, because a client cannot observe the stages of a POST that does not
return until it is finished.

This is the column that lets it observe them. `POST .../quizzes` now returns as soon as
the session exists, generation continues in the background, and the client polls
`GET /quizzes/{id}` for `status` and `generationStage`.

Nullable, and not backfilled. A session that finished before this column existed has no
stage, and writing `READY` onto it would be inventing a history: those sessions were
never observed progressing through anything.

Revision ID: 019_add_quiz_generation_stage
Revises: 018_add_quiz_generation_ms
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "019_add_quiz_generation_stage"
down_revision = "018_add_quiz_generation_ms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("QuizSession", sa.Column("generationStage", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("QuizSession", "generationStage")
