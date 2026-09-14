"""Which database columns are Postgres enums that the ORM believes are strings?

**Read-only. Runs no DDL and writes nothing.** Safe against production, which is the point: this class of
bug is invisible anywhere else.

The failure it exists to find, observed in production on 2026-09-14::

    operator does not exist: "FeedbackStatus" = character varying

`Feedback.status` is a real Postgres enum in production, created in the Prisma era. The SQLAlchemy model
maps it as `String`, so asyncpg binds the comparison value as `varchar`, and Postgres has no
`enum = varchar` operator. Every query of that shape fails.

**It cannot be caught outside production**, and that is the deeper problem. Staging's tables were created
by `src/init_schema.py`'s `create_all`, which renders `Mapped[str]` as `varchar`; production's were created
by Prisma with real enum types. So the two databases disagree about the type of every enum column, and a
query that works on staging can fail in production for reasons no test can see. Verified: staging has zero
user-defined enum columns.

    .venv/bin/python scripts/audit_enum_columns.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import asyncio
import glob
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import String, Text, text  # noqa: E402

from src.shared.database import connect_db, disconnect_db, get_session_factory  # noqa: E402


def load_models() -> dict[tuple[str, str], object]:
    """Every mapped column, keyed by `(table, column)`. Discovered rather than listed."""
    domains_dir = Path(__file__).resolve().parents[1] / "src" / "domains"
    for path in sorted(glob.glob(os.path.join(str(domains_dir), "*", "db_models.py"))):
        domain = Path(path).parent.name
        try:
            importlib.import_module(f"src.domains.{domain}.db_models")
        except (
            Exception
        ) as exc:  # pragma: no cover - a domain that cannot import is not this tool's job
            print(f"  (could not import {domain}.db_models: {type(exc).__name__})")

    from src.shared.database.base import Base

    mapped: dict[tuple[str, str], object] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            mapped[(table.name, column.name)] = column.type
    return mapped


async def main() -> int:
    mapped = load_models()
    print(f"{len(mapped)} mapped columns known to the ORM\n")

    await connect_db()
    factory = get_session_factory()
    async with factory() as session:
        # `public` only: Supabase's own `auth` schema has enums that are none of our business.
        enum_columns = (
            await session.execute(
                text("""
                SELECT c.table_name, c.column_name, c.udt_name
                FROM information_schema.columns c
                JOIN pg_type t ON t.typname = c.udt_name
                WHERE c.table_schema = 'public'
                  AND t.typtype = 'e'
                ORDER BY c.table_name, c.column_name
                """)
            )
        ).all()
    await disconnect_db()

    if not enum_columns:
        print("No user-defined enum columns in this database.")
        print("That is the signature of a `create_all` schema, not a Prisma one.")
        return 0

    print(f"{len(enum_columns)} enum column(s) in the database:\n")
    mismatches: list[tuple[str, str, str]] = []
    for table_name, column_name, udt in enum_columns:
        orm_type = mapped.get((table_name, column_name))
        if orm_type is None:
            verdict = "not mapped by the ORM"
        elif isinstance(orm_type, String | Text):
            # The failing shape: the ORM will bind comparison values as varchar.
            verdict = f"MISMATCH: ORM says {orm_type.__class__.__name__}"
            mismatches.append((table_name, column_name, udt))
        else:
            verdict = f"ok: ORM says {orm_type.__class__.__name__}"
        print(f"  {table_name}.{column_name:<28} db={udt:<28} {verdict}")

    if mismatches:
        print(
            f"\n{len(mismatches)} column(s) will fail any direct string comparison in this database.\n"
            "Each one needs either a cast at the comparison site or an ORM type that matches. Casting\n"
            "to text is the portable fix, because it is correct whether the column is an enum here and\n"
            "a varchar on staging."
        )
        print("\nGrep for comparisons to fix:")
        for table_name, column_name, _ in mismatches:
            print(f"  {table_name}.{column_name}")
        return 1

    print("\nEvery enum column is mapped with a matching ORM type.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
