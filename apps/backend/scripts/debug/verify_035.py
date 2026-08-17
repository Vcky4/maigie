"""Confirm migration 035 landed: table, columns, FK rules, index and check constraint.

Run through `scripts/db_direct.py`, which picks a host that supports DDL and plain queries. Written because
"alembic said upgrade" and "the column is there" are different claims, and this programme has already found
one migration that reported success after rolling itself back on an over-long revision id.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)


async def main() -> None:
    # `statement_cache_size=0`: pgbouncer breaks asyncpg's prepared statements, which is what produces the
    # DuplicatePreparedStatementError this repo has hit in ad-hoc scripts before.
    engine = create_async_engine(URL, connect_args={"statement_cache_size": 0})
    async with engine.connect() as conn:
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        print(f"alembic_version       : {version}")

        cols = (
            await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default"
                    " FROM information_schema.columns"
                    " WHERE table_name = 'TopicIllustration' ORDER BY ordinal_position"
                )
            )
        ).fetchall()
        print(f"columns               : {len(cols)}")
        for name, dtype, nullable, default in cols:
            print(f"  {name:<12} {dtype:<26} null={nullable:<3} default={default}")

        fks = (
            await conn.execute(
                text(
                    "SELECT kcu.column_name, rc.delete_rule"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.key_column_usage kcu"
                    "   ON tc.constraint_name = kcu.constraint_name"
                    " JOIN information_schema.referential_constraints rc"
                    "   ON tc.constraint_name = rc.constraint_name"
                    " WHERE tc.table_name = 'TopicIllustration'"
                    "   AND tc.constraint_type = 'FOREIGN KEY'"
                )
            )
        ).fetchall()
        print(f"foreign keys          : {[(c, r) for c, r in fks]}")

        checks = (
            await conn.execute(
                text(
                    "SELECT con.conname, pg_get_constraintdef(con.oid)"
                    " FROM pg_constraint con"
                    " JOIN pg_class rel ON rel.oid = con.conrelid"
                    " WHERE rel.relname = 'TopicIllustration' AND con.contype = 'c'"
                )
            )
        ).fetchall()
        print(f"check constraints     : {[(n, d) for n, d in checks]}")

        idx = (
            await conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'TopicIllustration'")
            )
        ).fetchall()
        print(f"indexes               : {[n for (n,) in idx]}")

        # The constraint is the part worth proving rather than reading: a row with neither a diagram nor an
        # equation must be refused, because it renders as a blank panel.
        try:
            await conn.execute(
                text(
                    'INSERT INTO "TopicIllustration" (id, "topicId", "userId", "createdAt")'
                    " VALUES ('verify-035-empty', 'nope', 'nope', now())"
                )
            )
            print("empty row refused     : NO — the constraint is not doing its job")
        except Exception as error:
            kind = type(error).__name__
            # A foreign-key violation would also land here and would not prove the check constraint, so the
            # message is inspected rather than the exception type alone.
            proved = "TopicIllustration_has_content" in str(error)
            print(f"empty row refused     : YES ({kind}, by the check: {proved})")

    await engine.dispose()


asyncio.run(main())
