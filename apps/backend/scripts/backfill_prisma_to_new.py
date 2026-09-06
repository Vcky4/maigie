"""One-off ETL: copy data from the legacy Prisma schema into the new (create_all + stamp) schema.

Option B of the production migration. The new schema is built by `init_schema` (create_all + alembic
stamp head); this script backfills the legacy data into it.

Usage (both DBs on the same local server for the rehearsal):
    OLD_URL=postgresql://user@localhost:5432/maigie_old \
    NEW_URL=postgresql://user@localhost:5432/maigie_new \
    python scripts/backfill_prisma_to_new.py [--dry-run]

Mapping rules (from migrations 002/031/032):
  * Table renames: Circle* -> Space* (see TABLE_MAP).
  * Column renames: circleId->spaceId, isCircleRoom->isSpaceRoom, circlePlan*->spacePlan*.
  * Enum columns copy as text (new schema uses VARCHAR).
  * Only columns present in BOTH schemas are copied; new-only columns take their defaults.
  * Old-only tables with no new counterpart (Embedding, _prisma_migrations, ...) are skipped.
FK checks are deferred during load via session_replication_role=replica (superuser).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import asyncpg

TABLE_MAP = {
    "Circle": "Space",
    "CircleMember": "SpaceMember",
    "CircleChatGroup": "SpaceChatGroup",
    "CircleChatGroupMember": "SpaceChatGroupMember",
    "CircleInvite": "SpaceInvite",
    "CircleMemberStat": "SpaceMemberStat",
    "CircleSession": "SpaceSession",
    "CircleJoinRequest": "SpaceJoinRequest",
    "CircleSubscription": "SpaceSubscription",
    "CircleSeatAddon": "SpaceSeatAddon",
}

COLUMN_MAP = {
    "circleId": "spaceId",
    "isCircleRoom": "isSpaceRoom",
    "circlePlanActive": "spacePlanActive",
    "circlePlanCurrentPeriodEnd": "spacePlanCurrentPeriodEnd",
}

# Old tables that are intentionally not carried over.
SKIP_OLD = {"_prisma_migrations", "Embedding"}


async def columns(con, table):
    rows = await con.fetch(
        """
        SELECT column_name,
               is_identity,
               (column_default IS NOT NULL) AS has_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1
        ORDER BY ordinal_position
        """,
        table,
    )
    return rows


async def table_names(con):
    rows = await con.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    return {r["table_name"] for r in rows}


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


async def main():
    dry = "--dry-run" in sys.argv
    old_url = os.environ["OLD_URL"].replace("postgresql+asyncpg://", "postgresql://")
    new_url = os.environ["NEW_URL"].replace("postgresql+asyncpg://", "postgresql://")
    # Passed as startup parameters (server_settings), NOT runtime `SET`: Supabase's pooler runs in
    # transaction mode, where a runtime `SET` does not persist across statements (each may land on a
    # different backend), but startup parameters are tracked per session by the pooler. search_path
    # keeps unqualified identifiers resolving to public; session_replication_role=replica defers FK
    # checks on the target so rows can be inserted in any order.
    old = await asyncpg.connect(
        old_url, statement_cache_size=0, server_settings={"search_path": "public"}
    )
    new = await asyncpg.connect(
        new_url,
        statement_cache_size=0,
        server_settings={"search_path": "public"},
    )

    new_tables = await table_names(new)
    old_tables = sorted(await table_names(old))

    total_rows = 0
    skipped = []
    identity_tables = []
    for t_old in old_tables:
        if t_old in SKIP_OLD:
            skipped.append((t_old, "explicit skip"))
            continue
        t_new = TABLE_MAP.get(t_old, t_old)
        if t_new not in new_tables:
            skipped.append((t_old, f"no target table '{t_new}' in new schema"))
            continue

        old_cols = await columns(old, t_old)
        new_col_rows = await columns(new, t_new)
        new_col_names = {c["column_name"] for c in new_col_rows}
        new_identity = {c["column_name"] for c in new_col_rows if c["is_identity"] == "YES"}

        pairs = []  # (old_col, new_col)
        dropped_cols = []
        for c in old_cols:
            oc = c["column_name"]
            nc = COLUMN_MAP.get(oc, oc)
            if nc in new_col_names:
                pairs.append((oc, nc))
            else:
                dropped_cols.append(oc)
        if not pairs:
            skipped.append((t_old, "no overlapping columns"))
            continue

        src_count = await old.fetchval(f"SELECT count(*) FROM {q(t_old)}")
        has_identity = bool(new_identity & {nc for _, nc in pairs})
        if has_identity:
            identity_tables.append(t_new)

        line = f"{t_old:32s} -> {t_new:32s} rows={src_count:<6d} cols={len(pairs)}"
        if dropped_cols:
            line += f"  (dropped old cols: {', '.join(dropped_cols[:6])}{'…' if len(dropped_cols) > 6 else ''})"
        print(line)

        if dry or src_count == 0:
            total_rows += src_count if src_count else 0
            continue

        select_cols = ", ".join(q(oc) for oc, _ in pairs)
        rows = await old.fetch(f"SELECT {select_cols} FROM {q(t_old)}")
        insert_cols = ", ".join(q(nc) for _, nc in pairs)
        placeholders = ", ".join(f"${i+1}" for i in range(len(pairs)))
        override = "OVERRIDING SYSTEM VALUE " if has_identity else ""
        stmt = f"INSERT INTO {q(t_new)} ({insert_cols}) {override}VALUES ({placeholders})"
        try:
            # One transaction per table: the pooler runs in transaction mode, so `SET LOCAL` holds
            # for the duration of this transaction (which is pinned to one backend). replica disables
            # FK triggers, so parent/child insert order does not matter.
            async with new.transaction():
                await new.execute("SET LOCAL session_replication_role = replica")
                await new.executemany(stmt, [tuple(r) for r in rows])
            total_rows += len(rows)
        except Exception as e:
            print(f"    !! FAILED inserting into {t_new}: {type(e).__name__}: {str(e)[:160]}")

    print("\n--- skipped ---")
    for t, why in skipped:
        print(f"  {t}: {why}")
    print(f"\ntotal rows {'to copy' if dry else 'copied'}: {total_rows}")
    if not dry and identity_tables:
        print("\n--- resetting identity sequences ---")
        for t in identity_tables:
            # reset each identity column's sequence to max(col)+1
            idcols = await new.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1 AND is_identity='YES'",
                t,
            )
            for r in idcols:
                col = r["column_name"]
                try:
                    await new.execute(
                        f"SELECT setval(pg_get_serial_sequence('{q(t)}', '{col}'), "
                        f"COALESCE((SELECT MAX({q(col)}) FROM {q(t)}), 1))"
                    )
                except Exception as e:
                    print(f"    seq reset {t}.{col} failed: {str(e)[:80]}")

    # Verbatim carry: preserve every old table that has no new target, exactly as-is, so no data is
    # thrown away (admin/CRM tables, legacy features with no look-alike, etc.). Only Prisma's own
    # bookkeeping and the superseded Embedding table are dropped. Restored pre-data + data only
    # (no FKs/indexes) to avoid cross-reference failures against renamed/absent tables.
    if not dry and "--carry" in sys.argv:
        dump_file = os.environ["DUMP_FILE"]
        carry = []
        for t_old in old_tables:
            if t_old in ("_prisma_migrations", "Embedding"):
                continue
            if t_old in TABLE_MAP:  # already mapped to a Space* table
                continue
            if t_old in new_tables:  # already a target we backfilled into
                continue
            carry.append(t_old)
        print(f"\n--- verbatim carry: {len(carry)} tables ---")
        print("  " + ", ".join(carry))
        args = []
        for t in carry:
            args += ["-t", t]
        for section in ("pre-data", "data"):
            res = subprocess.run(
                ["pg_restore", "-d", new_url, f"--section={section}",
                 "--no-owner", "--no-privileges", *args, dump_file],
                capture_output=True, text=True,
            )
            errs = res.stderr.lower().count("error:")
            print(f"  {section}: pg_restore errors={errs}")

        # Preservation check: every non-dropped old table's row count must appear in new.
        print("\n--- preservation check ---")
        misses = 0
        for t_old in old_tables:
            if t_old in ("_prisma_migrations", "Embedding"):
                continue
            t_new = TABLE_MAP.get(t_old, t_old)
            o = await old.fetchval(f"SELECT count(*) FROM {q(t_old)}")
            try:
                n = await new.fetchval(f"SELECT count(*) FROM {q(t_new)}")
            except Exception:
                n = None
            if o != n:
                print(f"  LOSS: {t_old}({o}) -> {t_new}({n})")
                misses += 1
        print(f"tables with row-count loss: {misses}")
        new_total = await new.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
        user_rows = await new.fetchval('SELECT count(*) FROM "User"')
        print(f"new table count: {new_total}; User rows: {user_rows}")

    await old.close()
    await new.close()


if __name__ == "__main__":
    asyncio.run(main())
