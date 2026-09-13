"""Reconcile admin staff roles before the least-privilege default takes effect.

`docs/ADMIN_DASHBOARD_PLAN.md` Decision 4 changed `shared.auth.dependencies._get_staff_role`: a
`role='ADMIN'` user whose `adminStaffRole` is NULL used to be treated as `SUPER_ADMIN`, and is now
treated as `CONTENT_MANAGER`. That is the correct default — the elevated role should only ever be
granted explicitly — but it is a behaviour change, and any existing administrator who predates the
column will silently lose super-admin access the moment the new default ships. This is Open
Question 4.

This script makes that population visible and, only when explicitly asked, fixes it by writing an
explicit `adminStaffRole='SUPER_ADMIN'` onto the admins who were relying on the old default.

**Read-only by default.** With no flags it runs `SELECT`s only — safe against production, which is
the one place the answer exists. `--apply` performs the single, bounded, reversible write described
below and nothing else.

Usage:
    python scripts/reconcile_admin_staff_roles.py                # report only (no writes)
    python scripts/reconcile_admin_staff_roles.py --apply        # promote NULL admins -> SUPER_ADMIN
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

# The admins the new default silently downgrades: platform admins with no explicit staff role. These
# are exactly the rows `--apply` promotes, because they were operating as super admins under the old
# default and nothing else recorded that intent.
NULL_ADMINS = (
    'SELECT id, email, name, "isActive" FROM "User" '
    "WHERE role = 'ADMIN' AND \"adminStaffRole\" IS NULL "
    "ORDER BY email"
)

ROLE_BREAKDOWN = (
    "SELECT COALESCE(\"adminStaffRole\", '(null -> CONTENT_MANAGER)') AS staff_role, COUNT(*) "
    "FROM \"User\" WHERE role = 'ADMIN' GROUP BY 1 ORDER BY 2 DESC"
)

PROMOTE_NULLS = (
    'UPDATE "User" SET "adminStaffRole" = \'SUPER_ADMIN\' '
    "WHERE role = 'ADMIN' AND \"adminStaffRole\" IS NULL"
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Promote every role='ADMIN' user with a NULL adminStaffRole to SUPER_ADMIN.",
    )
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Mirror the app's asyncpg settings: the deployment sits behind PgBouncer in transaction mode,
    # which rejects the prepared statements asyncpg caches by default.
    engine = create_async_engine(
        url,
        poolclass=None,
        connect_args={
            "prepared_statement_cache_size": 0,
            "statement_cache_size": 0,
            "server_settings": {"search_path": "public, extensions"},
        },
    )
    try:
        async with engine.connect() as conn:
            print("  Admin staff-role breakdown (role='ADMIN'):")
            for staff_role, count in (await conn.execute(text(ROLE_BREAKDOWN))).all():
                print(f"    {staff_role}: {count}")

            null_admins = (await conn.execute(text(NULL_ADMINS))).all()
            print(
                f"\n  {len(null_admins)} admin(s) have NO explicit staff role and will drop to "
                "CONTENT_MANAGER under the new default:"
            )
            for _id, email, name, is_active in null_admins:
                suffix = "" if is_active else "  [inactive]"
                print(f"    - {email} ({name or 'no name'}){suffix}")

            if not args.apply:
                print(
                    "\n  Report only - no changes made. Decide which of the above should be "
                    "SUPER_ADMIN.\n"
                    "  Re-run with --apply to promote ALL of them, or set the elevated role\n"
                    "  individually via POST /api/v1/admin/staff/role and leave the rest as\n"
                    "  content managers."
                )
                return 0

            if not null_admins:
                print("\n  Nothing to apply — every admin already has an explicit staff role.")
                return 0

            # The one write. Bounded to NULL admins, and reversible (set back to NULL, or demote via
            # the staff-role endpoint). Committed explicitly.
            async with engine.begin() as write_conn:
                result = await write_conn.execute(text(PROMOTE_NULLS))
            print(
                f"\n  APPLIED: promoted {result.rowcount} admin(s) to SUPER_ADMIN. "
                "Verify with a re-run (report should now show zero NULL admins)."
            )
    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
