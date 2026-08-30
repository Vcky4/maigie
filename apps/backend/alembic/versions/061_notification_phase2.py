"""Backfill mobile installations and enforce duplicate-free delivery plans."""

import sqlalchemy as sa

from alembic import op

revision = "061_notification_phase2"
down_revision = "060_notification_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "PushInstallation",
        sa.Column("revocationSecretHash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "PushInstallation_installationId_revocationSecretHash_idx",
        "PushInstallation",
        ["installationId", "revocationSecretHash"],
        unique=False,
        postgresql_where=sa.text('"revocationSecretHash" IS NOT NULL'),
    )

    # A destination may have at most one plan for a notification/channel. The
    # partial form leaves non-addressed channels available for later phases.
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            '"NotificationDelivery_notification_channel_destination_key" '
            'ON "NotificationDelivery" ("notificationId", channel, "destinationId") '
            'WHERE "destinationId" IS NOT NULL'
        )
    )

    # Legacy ids use only a digest, never token material. Existing canonical
    # rows win token/installation conflicts because they may be newer than the
    # retained compatibility record. Re-running this statement is a no-op.
    op.execute(
        sa.text(
            """
            INSERT INTO "PushInstallation" (
                id, "userId", "installationId", platform, transport, token,
                "permissionState", "lastSeenAt", "lastRegisteredAt",
                "createdAt", "updatedAt", "failureCount"
            )
            SELECT
                'legacy-' || substring(md5(dt.id), 1, 18),
                dt."userId",
                'legacy-' || substring(md5(dt.token), 1, 18),
                dt.platform,
                CASE
                    WHEN dt.token ~ '^(ExponentPushToken|ExpoPushToken)\\[[^]]+\\]$'
                    THEN 'EXPO'
                    ELSE 'FCM'
                END,
                dt.token,
                'DEFAULT',
                COALESCE(dt."updatedAt", dt."createdAt"),
                COALESCE(dt."updatedAt", dt."createdAt"),
                dt."createdAt",
                dt."updatedAt",
                0
            FROM "DeviceToken" AS dt
            WHERE dt.platform IN ('IOS', 'ANDROID')
              AND NOT EXISTS (
                  SELECT 1 FROM "PushInstallation" AS pi WHERE pi.token = dt.token
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM "PushInstallation" AS pi
                  WHERE pi."userId" = dt."userId"
                    AND pi."installationId" =
                        'legacy-' || substring(md5(dt.token), 1, 18)
                    AND pi.transport = CASE
                        WHEN dt.token ~ '^(ExponentPushToken|ExpoPushToken)\\[[^]]+\\]$'
                        THEN 'EXPO'
                        ELSE 'FCM'
                    END
              )
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Retain migrated installations and legacy DeviceToken evidence. Deleting
    # rows here could remove registrations updated by the Phase 2 runtime.
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS " '"NotificationDelivery_notification_channel_destination_key"'
        )
    )
    op.drop_index(
        "PushInstallation_installationId_revocationSecretHash_idx",
        table_name="PushInstallation",
    )
    op.drop_column("PushInstallation", "revocationSecretHash")
