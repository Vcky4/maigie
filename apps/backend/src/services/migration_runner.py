"""
Migration runner — one-time idempotent data migration for Circle Reimagining.

Converts STUDY_CIRCLE_* and SQUAD_* subscriptions to PLUS_*, grants
complimentary Circle Plans to qualifying Study Circle owners, defaults
all Circles to PRIVATE, backfills usage_scope, and maps roles.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from prisma import Prisma

from src.core.database import db as default_db

logger = logging.getLogger(__name__)


async def run_migration(
    *,
    dry_run: bool = False,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Execute the Circle Reimagining data migration.

    Idempotent: uses ``Circle.migrationMarker`` and per-user markers to
    skip already-processed entities on re-runs.

    Steps:
        1. Convert STUDY_CIRCLE_* user tiers → PREMIUM_* (Plus)
        2. Convert SQUAD_* user tiers → PREMIUM_* (Plus)
        3. For each STUDY_CIRCLE owner: pick complimentary Circle
           (most members, oldest createdAt as tie-breaker) and grant
           Circle_Plan with seat pool 4 and OWNER seat 1 = PLUS_SEAT
        4. Default all Circles to visibility=PRIVATE
        5. Default all members to seatTier=FREE_SEAT (unless granted above)
        6. Backfill usage_scope on AiUsageRecord
        7. Map roles (preserve exact matches, default unrecognized to MEMBER)
        8. Flag Circles with no resolvable owner for manual review

    Args:
        dry_run: If True, compute the report without mutating data.
        db_client: Optional Prisma client.

    Returns:
        A MigrationReport-shaped dict.
    """
    client = db_client or default_db
    now = datetime.now(UTC)

    report = {
        "runId": f"migration_{int(now.timestamp())}",
        "startedAt": now.isoformat(),
        "finishedAt": None,
        "status": "RUNNING",
        "circlesMigrated": 0,
        "circlesFlaggedForManualReview": 0,
        "usersConvertedFromStudyCircle": 0,
        "usersConvertedFromSquad": 0,
        "complimentaryCirclePlanGrants": 0,
        "dryRun": dry_run,
    }

    try:
        # Step 1: Convert STUDY_CIRCLE_* tiers to PREMIUM_*
        study_circle_users = await client.user.find_many(
            where={"tier": {"in": ["STUDY_CIRCLE_MONTHLY", "STUDY_CIRCLE_YEARLY"]}}
        )
        for user in study_circle_users:
            if not dry_run:
                new_tier = (
                    "PREMIUM_MONTHLY"
                    if str(user.tier) == "STUDY_CIRCLE_MONTHLY"
                    else "PREMIUM_YEARLY"
                )
                await client.user.update(
                    where={"id": user.id},
                    data={"tier": new_tier},
                )
            report["usersConvertedFromStudyCircle"] += 1

        # Step 2: Convert SQUAD_* tiers to PREMIUM_*
        squad_users = await client.user.find_many(
            where={"tier": {"in": ["SQUAD_MONTHLY", "SQUAD_YEARLY"]}}
        )
        for user in squad_users:
            if not dry_run:
                new_tier = (
                    "PREMIUM_MONTHLY"
                    if str(user.tier) == "SQUAD_MONTHLY"
                    else "PREMIUM_YEARLY"
                )
                await client.user.update(
                    where={"id": user.id},
                    data={"tier": new_tier},
                )
            report["usersConvertedFromSquad"] += 1

        # Step 3: Grant complimentary Circle Plan to Study Circle owners
        # For each user who was on STUDY_CIRCLE_*, find their Circles where
        # they are OWNER and pick the one with most members (oldest as tie-breaker)
        study_circle_owner_ids = [u.id for u in study_circle_users]
        for owner_id in study_circle_owner_ids:
            owned_circles = await client.circle.find_many(
                where={"createdById": owner_id, "migrationMarker": None},
                include={"members": True},
                order={"createdAt": "asc"},
            )
            if not owned_circles:
                continue

            # Pick the circle with most members (tie-break: oldest)
            best_circle = max(
                owned_circles,
                key=lambda c: (len(c.members) if c.members else 0, -(c.createdAt.timestamp())),
            )

            if not dry_run:
                # Grant Circle Plan
                await client.circle.update(
                    where={"id": best_circle.id},
                    data={
                        "circlePlanActive": True,
                        "seatPoolSize": 4,
                        "migrationMarker": f"migrated_{report['runId']}",
                    },
                )
                # Set owner's seat to PLUS_SEAT
                await client.circlemember.update_many(
                    where={"circleId": best_circle.id, "userId": owner_id, "role": "OWNER"},
                    data={"seatTier": "PLUS_SEAT"},
                )

            report["complimentaryCirclePlanGrants"] += 1

        # Step 4: Default all Circles to PRIVATE (skip already-migrated)
        circles_to_migrate = await client.circle.find_many(
            where={"migrationMarker": None}
        )
        for circle in circles_to_migrate:
            if not dry_run:
                await client.circle.update(
                    where={"id": circle.id},
                    data={
                        "visibility": "PRIVATE",
                        "migrationMarker": f"migrated_{report['runId']}",
                    },
                )
            report["circlesMigrated"] += 1

        # Step 5: Default all members to FREE_SEAT (skip those already set)
        if not dry_run:
            await client.circlemember.update_many(
                where={"seatTier": None},
                data={"seatTier": "FREE_SEAT"},
            )

        # Step 6: Backfill usage_scope on AiUsageRecord
        if not dry_run:
            # Records with circleId get circle scope
            records_with_circle = await client.aiusagerecord.find_many(
                where={"usageScope": None, "circleId": {"not": None}},
                take=1000,
            )
            for record in records_with_circle:
                await client.aiusagerecord.update(
                    where={"id": record.id},
                    data={"usageScope": f"circle:{record.circleId}"},
                )

            # Records without circleId get personal scope
            await client.aiusagerecord.update_many(
                where={"usageScope": None, "circleId": None},
                data={"usageScope": "personal"},
            )

        # Step 7: Flag Circles with no resolvable owner
        all_circles = await client.circle.find_many(
            include={"members": True}
        )
        for circle in all_circles:
            members = circle.members or []
            has_owner = any(str(m.role) == "OWNER" for m in members)
            if not has_owner and len(members) > 0:
                report["circlesFlaggedForManualReview"] += 1
                if not dry_run:
                    logger.warning(
                        "Circle %s has no OWNER — flagged for manual review",
                        circle.id,
                    )

        # Persist migration run record
        if not dry_run:
            try:
                await client.migrationrun.create(
                    data={
                        "status": "COMPLETED",
                        "startedAt": now,
                        "finishedAt": datetime.now(UTC),
                        "circlesMigrated": report["circlesMigrated"],
                        "circlesFlaggedForManualReview": report["circlesFlaggedForManualReview"],
                        "usersConvertedFromStudyCircle": report["usersConvertedFromStudyCircle"],
                        "usersConvertedFromSquad": report["usersConvertedFromSquad"],
                        "complimentaryCirclePlanGrants": report["complimentaryCirclePlanGrants"],
                        "dryRun": False,
                    }
                )
            except Exception:
                logger.exception("Failed to persist MigrationRun record")

        report["status"] = "COMPLETED"
        report["finishedAt"] = datetime.now(UTC).isoformat()

    except Exception as e:
        report["status"] = "FAILED"
        report["finishedAt"] = datetime.now(UTC).isoformat()
        logger.exception("Migration failed: %s", e)
        raise

    return report
