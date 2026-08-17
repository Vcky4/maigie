"""Make note versions writable and readable, and stop new documents defaulting to public.

Two unrelated-looking changes, both about a table telling the truth about itself.

## `NoteHistory` gains a title, an index, and — for the first time — a producer

The table has existed since the Prisma schema and holds zero rows. Nothing writes it and nothing
reads it: `Note.history` is declared `lazy="noload"`, there is no repository method, no service
call, no route. Migration 022's docstring already names it as the example to avoid — "rows written
that nothing ever reads" — except it is worse than that, because nothing writes them either.

The reason to keep it rather than drop it is `POST /notes/{id}/retake`, which sends a learner's note
to an LLM and overwrites `Note.content` with the rewrite. Their original prose is destroyed, in
place, with no copy anywhere. `POST /notes/{id}/summary` is safe by comparison — it writes a
different column — but retake is a one-way door on the only copy of something the learner wrote.
That is what a version table is for, so this migration gives it a producer instead of deleting it.

`title` is added and snapshotted alongside the content, for the reason migration 029 snapshots a
knowledge-check question: a version has to stay self-describing. Restoring content into a note that
has since been retitled otherwise produces a version list where every entry is labelled by the
note's *current* name, which is the one thing a version is not. It is `NOT NULL` because the table
is empty, so there is no existing row to backfill and no reason to publish a weaker contract than
the data has. `content` stays nullable, matching `Note.content`: a note may genuinely have none.

The index carries the only read there is — this note's versions, newest first.

## `GeneratedDocument."isPublic"` server default `true` -> `false`

A column whose default is "share this with the world" is the wrong way round, and it is not
hypothetical. `handle_generate_document` — the chat skill — inserts a document row with raw SQL, and
that insert both relies on the default elsewhere and passes `true` explicitly. Any future writer
that omits the column publishes the learner's document without being asked.

The ORM's own default is already `false`, so ORM-mediated creation is unaffected. This aligns the
database with it so the two cannot disagree, and so the safe outcome is the one you get by saying
nothing. Existing rows are left exactly as they are: a default change is not a data change, and
three documents are genuinely published.

Revision ID: 033_add_note_versions
Revises: 032_retire_circle_cols
Create Date: 2026-08-17

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back. The id
below is 20 characters.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "033_add_note_versions"
down_revision = "032_retire_circle_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("NoteHistory", sa.Column("title", sa.Text(), nullable=False))
    op.create_index(
        "NoteHistory_noteId_createdAt_idx",
        "NoteHistory",
        ["noteId", "createdAt"],
    )

    op.alter_column(
        "GeneratedDocument",
        "isPublic",
        existing_type=sa.Boolean(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "GeneratedDocument",
        "isPublic",
        existing_type=sa.Boolean(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    op.drop_index("NoteHistory_noteId_createdAt_idx", table_name="NoteHistory")
    op.drop_column("NoteHistory", "title")
