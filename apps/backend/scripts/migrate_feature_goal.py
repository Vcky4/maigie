"""Feature migration: legacy Prisma Goal → new progress-domain Goal (+ create goal-lifecycle tables).

Root cause this also repairs: `init_schema` never imported the `progress` domain, so `create_all`
skipped it. The verbatim carry then left a *legacy-shaped* "Goal" table (old columns, 22 rows)
squatting on the name the new model wants, and the four goal-lifecycle tables missing entirely.

This script, run against the migrated DB (legacy Goal present, new model absent):
  1. Renames the legacy "Goal" → "Goal_legacy" (and its constraints/indexes out of the Goal*
     namespace so the new table's names don't collide). Skipped if already migrated.
  2. Runs create_all (all domains imported) → creates the new "Goal" plus GoalMilestone,
     GoalScheduleChange, GoalLifecycleAction, GoalProgressSnapshot. Idempotent.
  3. Copies Goal_legacy → new Goal: circleId→spaceId, status enum→text, metricKind='manual'
     (legacy goals had no measurable source), naive timestamps read as UTC, and every optional FK
     (courseId/topicId/spaceId) nulled when it does not resolve to a migrated row (they are SET NULL
     links). targetValue/unit/currentValue/prepId are new and stay null.

Usage:
    TARGET_URL=postgresql://user@host:5432/db python scripts/migrate_feature_goal.py [--commit]
Dry run by default: does everything in one transaction and rolls back after printing the result.
"""

from __future__ import annotations

import asyncio
import glob
import importlib
import os
import sys


class _DryRun(Exception):
    pass


async def main() -> None:
    commit = "--commit" in sys.argv
    os.environ["DATABASE_URL"] = os.environ["TARGET_URL"]
    sys.path.insert(0, os.getcwd())  # run from apps/backend so `src` is importable

    for f in glob.glob("src/domains/*/db_models.py"):
        importlib.import_module("src.domains." + f.split("/")[2] + ".db_models")

    from sqlalchemy import text

    from src.shared.database.base import Base
    from src.shared.database.session import connect_db, get_session_factory

    await connect_db()
    engine = get_session_factory().kw["bind"]

    try:
        async with engine.begin() as conn:
            has_metric = await conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='Goal' AND column_name='metricKind')"
                )
            )
            legacy_exists = await conn.scalar(
                text("SELECT to_regclass('public.\"Goal_legacy\"') IS NOT NULL")
            )
            goal_exists = await conn.scalar(
                text("SELECT to_regclass('public.\"Goal\"') IS NOT NULL")
            )

            if goal_exists and not has_metric and not legacy_exists:
                print("  renaming legacy Goal -> Goal_legacy (with its constraints/indexes)")
                await conn.execute(text('ALTER TABLE "Goal" RENAME TO "Goal_legacy"'))
                await conn.execute(
                    text(
                        """
                        DO $$ DECLARE r record; BEGIN
                          FOR r IN SELECT conname FROM pg_constraint
                                   WHERE conrelid = '"Goal_legacy"'::regclass LOOP
                            EXECUTE format('ALTER TABLE "Goal_legacy" RENAME CONSTRAINT %I TO %I',
                                           r.conname, 'legacy_' || r.conname);
                          END LOOP;
                          FOR r IN SELECT indexname FROM pg_indexes
                                   WHERE schemaname='public' AND tablename='Goal_legacy'
                                     AND indexname NOT LIKE 'legacy_%' LOOP
                            EXECUTE format('ALTER INDEX %I RENAME TO %I',
                                           r.indexname, 'legacy_' || r.indexname);
                          END LOOP;
                        END $$;
                        """
                    )
                )
            elif has_metric:
                print("  Goal already has the new shape — skipping rename")

            # Create the new Goal + the four goal-lifecycle tables (and any other missing table).
            await conn.run_sync(Base.metadata.create_all)

            # Complete the ScheduleBlock schema. It was carried verbatim and create_all skips an
            # existing table, so the two columns the new model added (completedAt, startedAt) are
            # missing. Goal.schedules is a selectin relationship to ScheduleBlock, so a Goal cannot
            # even be read until these exist. Both are nullable with no default — safe on the
            # existing rows, and ADD COLUMN IF NOT EXISTS keeps this idempotent.
            await conn.execute(
                text(
                    'ALTER TABLE "ScheduleBlock" ADD COLUMN IF NOT EXISTS "completedAt" timestamptz'
                )
            )
            await conn.execute(
                text('ALTER TABLE "ScheduleBlock" ADD COLUMN IF NOT EXISTS "startedAt" timestamptz')
            )

            # Copy legacy rows into the new Goal, if a legacy table is present.
            legacy_now = await conn.scalar(
                text("SELECT to_regclass('public.\"Goal_legacy\"') IS NOT NULL")
            )
            if legacy_now:
                await conn.execute(
                    text(
                        """
                        INSERT INTO "Goal" (id, "userId", title, description, "targetDate", status,
                                            progress, "metricKind", "spaceId", "courseId", "topicId",
                                            "createdAt", "updatedAt")
                        SELECT g.id, g."userId", g.title, g.description,
                               g."targetDate" AT TIME ZONE 'UTC', g.status::text, g.progress, 'manual',
                               CASE WHEN g."circleId" IS NOT NULL
                                         AND EXISTS (SELECT 1 FROM "Space" s WHERE s.id = g."circleId")
                                    THEN g."circleId" ELSE NULL END,
                               CASE WHEN g."courseId" IS NOT NULL
                                         AND EXISTS (SELECT 1 FROM "Course" c WHERE c.id = g."courseId")
                                    THEN g."courseId" ELSE NULL END,
                               CASE WHEN g."topicId" IS NOT NULL
                                         AND EXISTS (SELECT 1 FROM "Topic" t WHERE t.id = g."topicId")
                                    THEN g."topicId" ELSE NULL END,
                               g."createdAt" AT TIME ZONE 'UTC', g."updatedAt" AT TIME ZONE 'UTC'
                        FROM "Goal_legacy" g
                        WHERE EXISTS (SELECT 1 FROM "User" u WHERE u.id = g."userId")
                          AND NOT EXISTS (SELECT 1 FROM "Goal" ng WHERE ng.id = g.id)
                        """
                    )
                )
                legacy_count = await conn.scalar(text('SELECT count(*) FROM "Goal_legacy"'))
            else:
                legacy_count = None

            new_count = await conn.scalar(text('SELECT count(*) FROM "Goal"'))
            print("\n--- result ---")
            print(f"  Goal_legacy rows: {legacy_count}")
            print(f"  new Goal rows:    {new_count}")
            for t in (
                "GoalMilestone",
                "GoalScheduleChange",
                "GoalLifecycleAction",
                "GoalProgressSnapshot",
            ):
                present = await conn.scalar(
                    text(f"SELECT to_regclass('public.\"{t}\"') IS NOT NULL")
                )
                print(f"  {t}: {'created' if present else 'MISSING'}")

            print("\n--- dangling-FK guards (must be 0) ---")
            checks = {
                "Goal.userId": 'SELECT count(*) FROM "Goal" g LEFT JOIN "User" u ON u.id=g."userId" WHERE u.id IS NULL',
                "Goal.spaceId": 'SELECT count(*) FROM "Goal" g WHERE g."spaceId" IS NOT NULL AND NOT EXISTS(SELECT 1 FROM "Space" s WHERE s.id=g."spaceId")',
                "Goal.courseId": 'SELECT count(*) FROM "Goal" g WHERE g."courseId" IS NOT NULL AND NOT EXISTS(SELECT 1 FROM "Course" c WHERE c.id=g."courseId")',
                "Goal.topicId": 'SELECT count(*) FROM "Goal" g WHERE g."topicId" IS NOT NULL AND NOT EXISTS(SELECT 1 FROM "Topic" t WHERE t.id=g."topicId")',
            }
            bad = 0
            for label, q in checks.items():
                c = await conn.scalar(text(q))
                if c:
                    bad += 1
                print(f"  {label:16s} {c}")
            if bad:
                raise RuntimeError(f"{bad} dangling-FK checks failed")
            if legacy_count is not None and new_count < legacy_count:
                raise RuntimeError(f"row loss: legacy={legacy_count} new={new_count}")

            if not commit:
                raise _DryRun()
        print("\nCOMMITTED ✅")
    except _DryRun:
        print("\nrolled back (dry run) — re-run with --commit to apply")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
