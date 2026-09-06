"""Retire the leftover `circleId` columns, and disconnect study sessions from spaces.

Three separate decisions, one migration, because they are all the same cleanup: the product says Space,
and the schema still says Circle in places that migration `002` did not reach.

## `Note.circleId` and `ExamPrep.circleId` — moved, then dropped

Both tables carry **both** `circleId` and `spaceId`. It would be reasonable to assume the old column is a
redundant copy and drop it, and that assumption would have destroyed data: on each table the single row
with `circleId` set has `spaceId` **null**. The old column holds the only copy of that association.

    Note      57 rows, circleId set on 1, spaceId null on that row
    ExamPrep  46 rows, circleId set on 1, spaceId null on that row

So the value is copied across first and the column dropped after, which is what `002` would have done had
it reached these tables. `Note.circleId` is in fact listed in its `COLUMN_RENAMES`; the migration returns
early on a database with no `Circle` table, which is how a listed rename ends up not having happened.

The copy is guarded with `WHERE spaceId IS NULL` so it can never overwrite a newer value with an older
one, and so re-running against a partially-migrated database is a no-op rather than a regression.

## `StudySession.spaceId` — dropped

Migration `031` renamed this column an hour ago to stop `POST /progress/sessions/start` from failing on a
column the ORM named and the table did not have. Renaming it was the right fix for that crash and the
wrong long-term answer: study sessions are not space-scoped work yet, nothing writes the field, and
`StartSessionRequest` accepts only `courseId` and `topicId`. A nullable column that no code path can fill
is a claim the schema makes and the product does not support.

Dropped rather than kept "for when we get there". The column costs nothing to add back in the migration
that first needs it, and at that point it will arrive with a writer, an index chosen for the queries that
read it, and a decision about what a space-scoped sitting means. 0 of 110 rows have a value, so nothing is
lost. Space attribution for a sitting remains derivable through `Course.spaceId` in the meantime.

## What is deliberately left standing

`CircleGroupCourseLink` (3 rows) and `CircleOwnershipTransfer` (0 rows) are still named for circles, and
no ORM model or line of code references either. `CircleGroupCourseLink` links a space chat group to a
course, which is a feature, not a naming artifact — its three rows are the only record of those links.
Dropping a table nothing currently reads but a future spaces feature would want is not a rename cleanup,
and it is not reversible, so it is not folded in here.

Revision ID: 032_retire_circle_cols
Revises: 031_rename_session_space
Create Date: 2026-08-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "032_retire_circle_cols"
down_revision = "031_rename_session_space"
branch_labels = None
depends_on = None

#: Tables carrying both names, where the old column's value must survive the drop.
_CARRY_OVER = ("Note", "ExamPrep")


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

    for table in _CARRY_OVER:
        if not _has_column(conn, table, "circleId"):
            continue
        if not _has_column(conn, table, "spaceId"):
            # No destination: this is the rename `002` describes, not a duplicate to reconcile.
            op.alter_column(table, "circleId", new_column_name="spaceId")
            continue

        # Move before dropping. `WHERE spaceId IS NULL` so a row already carrying a space keeps it.
        conn.execute(
            sa.text(
                f'UPDATE "{table}" SET "spaceId" = "circleId" '
                f'WHERE "circleId" IS NOT NULL AND "spaceId" IS NULL'
            )
        )
        op.execute(f'DROP INDEX IF EXISTS "{table}_circleId_idx"')
        op.drop_column(table, "circleId")

    if _has_column(conn, "StudySession", "spaceId"):
        op.execute('DROP INDEX IF EXISTS "StudySession_spaceId_idx"')
        op.drop_column("StudySession", "spaceId")


def downgrade() -> None:
    conn = op.get_bind()

    if not _has_column(conn, "StudySession", "spaceId"):
        op.add_column("StudySession", sa.Column("spaceId", sa.String(), nullable=True))
        op.create_index("StudySession_spaceId_idx", "StudySession", ["spaceId"])

    # The carried-over values are not moved back. They are in `spaceId`, which is where the product reads
    # them from, and splitting them across two columns again would recreate the ambiguity this removed.
    for table in _CARRY_OVER:
        if not _has_column(conn, table, "circleId"):
            op.add_column(table, sa.Column("circleId", sa.String(), nullable=True))
            op.create_index(f"{table}_circleId_idx", table, ["circleId"])
