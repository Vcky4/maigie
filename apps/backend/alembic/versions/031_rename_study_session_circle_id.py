"""Finish the Circle → Space rename on `StudySession`.

Migration `002` renamed `circleId` to `spaceId` across seventeen tables and listed them explicitly.
`StudySession` is not on that list, and the ORM was updated anyway — `StudySession.space_id` maps to a
`spaceId` column that has never existed on this table. Every read of the model therefore names a column
Postgres does not have, so `POST /progress/sessions/start` answered `500`:

    UndefinedColumnError: column StudySession.spaceId does not exist

That failure was invisible for as long as it existed, because the only caller was a web client asking
`/analytics/sessions/start`, a prefix nothing is mounted at. The `404` masked the `500` behind it. Fixing
the client's path is what surfaced this.

## Rename, not add-and-backfill

The column exists under the old name with an index on it, and 0 of 109 existing rows have a value — no
study session has ever been attributed to a space, which follows from nothing being able to write one:
`StartSessionRequest` accepts `courseId` and `topicId` only. So a rename moves no data, and it keeps the
index rather than leaving `StudySession_circleId_idx` indexing a column that no longer exists.

## What this deliberately does not touch

`Note` and `ExamPrep` each carry **both** `circleId` and `spaceId`. Their models read `spaceId`, which is
present, so both work; the `circleId` columns are dead weight left behind rather than a defect. Dropping
them is a destructive change to tables holding real data, it fixes nothing, and it belongs in its own
migration with its own decision. Recorded here so the next person reading this file knows they were seen
and left alone on purpose.

`CircleGroupCourseLink` and `CircleOwnershipTransfer` are still named for circles, tables and columns
both. They are outside the mounted domains and are likewise left alone.

Revision ID: 031_rename_session_space
Revises: 030_add_lesson_stage
Create Date: 2026-08-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "031_rename_session_space"
down_revision = "030_add_lesson_stage"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column)"
            ),
            {"table": table, "column": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Guarded both ways. A database created fresh from the ORM metadata already has `spaceId` and no
    # `circleId`, and an unguarded rename would fail on it — the same reason `002` checks for the `Circle`
    # table before renaming anything.
    if _has_column(conn, "StudySession", "spaceId"):
        return
    if not _has_column(conn, "StudySession", "circleId"):
        return

    op.alter_column("StudySession", "circleId", new_column_name="spaceId")
    # Alembic has no rename_index, and `IF EXISTS` covers a database whose index was named differently by
    # Prisma. A missing index is not worth failing a rename over; a wrongly-named one is worth fixing.
    op.execute(
        'ALTER INDEX IF EXISTS "StudySession_circleId_idx" RENAME TO "StudySession_spaceId_idx"'
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "StudySession", "spaceId"):
        return

    op.execute(
        'ALTER INDEX IF EXISTS "StudySession_spaceId_idx" RENAME TO "StudySession_circleId_idx"'
    )
    op.alter_column("StudySession", "spaceId", new_column_name="circleId")
