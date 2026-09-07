"""Data fix: flip `User.isOnboarded` back to false for users who have no LearningProfile.

Why: the migrated DB has zero `LearningProfile` rows (it is a net-new table with no legacy
source), yet 178 users carry `isOnboarded=true` from the legacy flag. Those users bypass the
onboarding guard (`ProtectedRoute` only redirects on `isOnboarded === false`) and land on Home
with no purpose/goal/subjects, so the system can't function as intended for them. Flipping them
to false re-routes them through the existing onboarding flow, which captures purpose → subjects →
goals and builds their LearningProfile.

Scope is precise and idempotent: only rows where `isOnboarded = true` AND no matching
`LearningProfile` row exists. Re-running after profiles are created is a no-op. Users with a
profile are never touched.

Usage:
    TARGET_URL=postgresql://user@host:5432/db python scripts/reset_onboarding_without_profile.py [--commit]

Dry run by default: reports the affected count inside a transaction and rolls back.
"""

from __future__ import annotations

import asyncio
import os
import sys


class _DryRun(Exception):
    pass


async def main() -> None:
    commit = "--commit" in sys.argv
    os.environ["DATABASE_URL"] = os.environ["TARGET_URL"]
    sys.path.insert(0, os.getcwd())  # run from apps/backend so `src` is importable

    from sqlalchemy import text

    from src.shared.database.session import connect_db, get_session_factory

    await connect_db()
    engine = get_session_factory().kw["bind"]

    # The set to flip: onboarded users with no profile row. LEFT JOIN + IS NULL rather than
    # NOT EXISTS is identical in result here; the join keeps the reporting query and the update
    # predicate visibly the same.
    predicate = (
        'FROM "User" u '
        'LEFT JOIN "LearningProfile" lp ON lp."userId" = u.id '
        'WHERE u."isOnboarded" = true AND lp.id IS NULL'
    )

    try:
        async with engine.begin() as conn:
            total_onboarded = await conn.scalar(
                text('SELECT count(*) FROM "User" WHERE "isOnboarded" = true')
            )
            profile_count = await conn.scalar(text('SELECT count(*) FROM "LearningProfile"'))
            to_flip = await conn.scalar(text("SELECT count(*) " + predicate))

            print(f"  onboarded users (isOnboarded=true): {total_onboarded}")
            print(f"  LearningProfile rows:               {profile_count}")
            print(f"  → will flip to false (no profile):  {to_flip}")

            result = await conn.execute(
                text(
                    'UPDATE "User" u SET "isOnboarded" = false '
                    'WHERE u."isOnboarded" = true '
                    'AND NOT EXISTS (SELECT 1 FROM "LearningProfile" lp WHERE lp."userId" = u.id)'
                )
            )
            flipped = getattr(result, "rowcount", None)
            print(f"  rows updated:                       {flipped}")

            remaining_true = await conn.scalar(
                text('SELECT count(*) FROM "User" WHERE "isOnboarded" = true')
            )
            print(f"  isOnboarded=true after update:      {remaining_true}")

            if not commit:
                raise _DryRun

        print("\n✅ Committed." if commit else "\n(dry run — rolled back; pass --commit to apply)")
    except _DryRun:
        print("\n(dry run — rolled back; pass --commit to apply)")


if __name__ == "__main__":
    asyncio.run(main())
