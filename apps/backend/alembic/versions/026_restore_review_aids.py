"""Restore the study aids that were deleted for having no column.

Four columns, each behind a piece of interface that a learner had and no longer does. They were
removed during stage 1 and stage 4 under one faulty rule — *no persisted field backs this, so the UI
goes* — when the correct conclusion was that the schema should gain one. This migration is that.

## The three flashcard aids

`FlashcardReviewPage` showed a **hint** on demand (a button and the `H` key), an **explanation** with
the answer, and a **memory hook** — a mnemonic — beside it. All three came from a fixture, and all
three disappeared when the page was migrated to real cards, because `Flashcard` has only `front` and
`back`.

Nullable, and no backfill. A card written before these existed has no hint, which is different from
having an empty one: the reader omits the control rather than offering a hint that turns out to be
blank. Generation fills them for new cards; a learner can write them by hand on any card.

Deliberately **not** one JSON column holding all three. They are three independent optional texts that
are read individually and edited individually, and packing them into a blob would mean a partial
update has to read-modify-write the whole object, so two edits racing lose one of the three.

## The lesson summary

`Topic` has a `title` and a `content` blob and nothing in between, so the lesson header's one-line
description had no source. The obvious substitutes are both wrong: the first section's summary
describes that section rather than the lesson, and the first paragraph of `content` is the opening of
the material, not a description of it — using either would put a sentence under the heading that says
something other than what it claims to.

Nullable for the same reason as the rest: a topic created before this has no summary, and the header
renders without one rather than with a fabricated line.

Revision ID: 026_restore_review_aids
Revises: 025_add_lesson_structure
Create Date: 2026-08-16

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back. The id
below is 23 characters.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "026_restore_review_aids"
down_revision = "025_add_lesson_structure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Text rather than String: an explanation is prose and can run to a paragraph, and a length limit
    # here would truncate a card's answer rather than reject it.
    op.add_column("Flashcard", sa.Column("hint", sa.Text(), nullable=True))
    op.add_column("Flashcard", sa.Column("explanation", sa.Text(), nullable=True))
    op.add_column("Flashcard", sa.Column("memoryHook", sa.Text(), nullable=True))

    # The lesson header's one-line description.
    op.add_column("Topic", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("Topic", "summary")
    op.drop_column("Flashcard", "memoryHook")
    op.drop_column("Flashcard", "explanation")
    op.drop_column("Flashcard", "hint")
