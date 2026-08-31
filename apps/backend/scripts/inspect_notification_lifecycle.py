"""Read-only, redacted notification lifecycle inspection.

Examples:
    python scripts/db_direct.py python scripts/inspect_notification_lifecycle.py --summary
    python scripts/db_direct.py python scripts/inspect_notification_lifecycle.py \
        --notification-id 641ce9756c6448dfba3198f6c

The output deliberately excludes user identifiers, addresses, provider tokens,
message bodies, action payload values, response metadata, and error details.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

_CHANNELS = ("MOBILE_PUSH", "WEB_PUSH", "EMAIL")


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise SystemExit("DATABASE_URL is not set")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def _json_default(value: object) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _mapping(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


async def _summary(conn: Any, channel: str | None) -> dict[str, Any]:
    condition = "AND channel = :channel" if channel else ""
    params = {
        "stale_seconds": int(os.environ.get("MOBILE_PUSH_STALE_SENDING_SECONDS", "600")),
        **({"channel": channel} if channel else {}),
    }
    rows = (
        await conn.execute(
            text(
                f"""SELECT channel, status, count(*) AS count,
                           min(CASE
                               WHEN status = 'SENDING' THEN "updatedAt"
                               WHEN status = 'ACCEPTED' THEN
                                   coalesce("nextAttemptAt", "acceptedAt", "eligibleAt")
                               ELSE coalesce("nextAttemptAt", "eligibleAt")
                           END) AS "oldestActionableAt"
                    FROM "NotificationDelivery"
                    WHERE (
                        (status IN ('PLANNED', 'QUEUED')
                         AND "eligibleAt" <= now()
                         AND ("nextAttemptAt" IS NULL OR "nextAttemptAt" <= now()))
                        OR (status = 'ACCEPTED'
                            AND ("nextAttemptAt" IS NULL OR "nextAttemptAt" <= now()))
                        OR (status = 'SENDING'
                            AND "updatedAt" <= now() - make_interval(secs => :stale_seconds))
                    )
                      AND ("expiresAt" IS NULL OR "expiresAt" > now())
                      {condition}
                    GROUP BY channel, status
                    ORDER BY channel, status"""
            ),
            params,
        )
    ).all()
    failures = (
        await conn.execute(
            text(
                f"""SELECT channel, "failureCode", count(*) AS count
                    FROM "NotificationDelivery"
                    WHERE status = 'FAILED'
                      AND "updatedAt" >= now() - interval '24 hours'
                      {condition}
                    GROUP BY channel, "failureCode"
                    ORDER BY channel, count(*) DESC"""
            ),
            params,
        )
    ).all()
    interactions = (
        await conn.execute(
            text(
                """SELECT surface, event, count(*) AS count
                   FROM "NotificationInteraction"
                   WHERE "occurredAt" >= now() - interval '24 hours'
                   GROUP BY surface, event
                   ORDER BY surface, event"""
            )
        )
    ).all()
    return {
        "generatedAt": datetime.now(UTC),
        "actionableDeliveries": [_mapping(row) for row in rows],
        "failuresLast24Hours": [_mapping(row) for row in failures],
        "interactionsLast24Hours": [_mapping(row) for row in interactions],
    }


async def _notification(conn: Any, notification_id: str) -> dict[str, Any]:
    notification = (
        await conn.execute(
            text(
                """SELECT n.id, n.type, n.category, n.urgency, n.status,
                          n."schemaVersion", n."sourceDomain", n."sourceEntityType",
                          n."eligibleAt", n."expiresAt",
                          n."readAt", n."dismissedAt", n."archivedAt", n."createdAt",
                          n."updatedAt", n.action ->> 'kind' AS "actionKind",
                          d."policyVersion", d."modelVersion", d."reasonCodes",
                          d."usedFallback", d."experimentId", d."latencyMs"
                   FROM "Notification" n
                   LEFT JOIN "NotificationDecision" d
                     ON d.id = n."intelligenceDecisionId"
                   WHERE n.id = :notification_id"""
            ),
            {"notification_id": notification_id},
        )
    ).first()
    if notification is None:
        raise SystemExit("notification not found")

    deliveries = (
        await conn.execute(
            text(
                """SELECT id, channel, provider, status, "attemptCount", "maxAttempts",
                          "suppressionReason", "failureCode", "eligibleAt", "nextAttemptAt",
                          "expiresAt", "acceptedAt", "deliveredAt", "failedAt",
                          "createdAt", "updatedAt"
                   FROM "NotificationDelivery"
                   WHERE "notificationId" = :notification_id
                   ORDER BY channel, "createdAt", id"""
            ),
            {"notification_id": notification_id},
        )
    ).all()
    delivery_ids = [row._mapping["id"] for row in deliveries]

    attempts: list[dict[str, Any]] = []
    if delivery_ids:
        attempts = [
            _mapping(row)
            for row in (
                await conn.execute(
                    text(
                        '''SELECT "deliveryId", "attemptNumber", "requestedAt", "durationMs",
                                  retryable, "errorCode", "createdAt"
                           FROM "NotificationDeliveryAttempt"
                           WHERE "deliveryId" = ANY(CAST(:delivery_ids AS text[]))
                           ORDER BY "deliveryId", "attemptNumber"'''
                    ),
                    {"delivery_ids": delivery_ids},
                )
            ).all()
        ]

    interactions = (
        await conn.execute(
            text(
                '''SELECT "deliveryId", event, surface, "occurredAt", "createdAt"
                   FROM "NotificationInteraction"
                   WHERE "notificationId" = :notification_id
                   ORDER BY "occurredAt", "createdAt"'''
            ),
            {"notification_id": notification_id},
        )
    ).all()
    return {
        "notification": _mapping(notification),
        "deliveries": [_mapping(row) for row in deliveries],
        "attempts": attempts,
        "interactions": [_mapping(row) for row in interactions],
    }


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_engine(_database_url(), echo=False)
    try:
        async with engine.connect() as conn:
            output = (
                await _notification(conn, args.notification_id)
                if args.notification_id
                else await _summary(conn, args.channel)
            )
        print(json.dumps(output, indent=2, default=_json_default, sort_keys=True))
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect canonical notification lifecycle without message or user content"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--notification-id", help="Canonical Notification.id")
    mode.add_argument("--summary", action="store_true", help="Aggregate actionable/failure counts")
    parser.add_argument("--channel", choices=_CHANNELS, help="Optional summary channel filter")
    args = parser.parse_args()
    if args.channel and not args.summary:
        parser.error("--channel requires --summary")
    if args.notification_id and len(args.notification_id) > 128:
        parser.error("--notification-id is too long")
    return args


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
