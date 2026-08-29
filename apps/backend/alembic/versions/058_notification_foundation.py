"""Add the durable notification planning and delivery foundation.

This revision is deliberately additive. Existing Notification rows, routes,
delivery semantics, and DeviceToken registrations continue unchanged while the
new platform can be introduced in shadow mode.
"""

import sqlalchemy as sa

from alembic import op

revision = "058_notification_foundation"
down_revision = "057_prep_result_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "NotificationDecision",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("notificationType", sa.String(), nullable=False),
        sa.Column("policyVersion", sa.String(), nullable=False),
        sa.Column("modelVersion", sa.String(), nullable=True),
        sa.Column("inputSnapshot", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("reasonCodes", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("usedFallback", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("experimentId", sa.String(), nullable=True),
        sa.Column("costUsd", sa.Float(), nullable=True),
        sa.Column("latencyMs", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "NotificationDecision_userId_createdAt_idx",
        "NotificationDecision",
        ["userId", "createdAt"],
    )
    op.create_index(
        "NotificationDecision_type_createdAt_idx",
        "NotificationDecision",
        ["notificationType", "createdAt"],
    )

    op.create_table(
        "PushInstallation",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("installationId", sa.String(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("token", sa.String(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("p256dhEncrypted", sa.Text(), nullable=True),
        sa.Column("authEncrypted", sa.Text(), nullable=True),
        sa.Column("appVersion", sa.String(), nullable=True),
        sa.Column("deviceLocale", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("permissionState", sa.String(length=16), nullable=True),
        sa.Column("lastSeenAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lastRegisteredAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabledAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failureCount", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "platform IN ('IOS', 'ANDROID', 'WEB')",
            name="PushInstallation_platform_check",
        ),
        sa.CheckConstraint(
            "transport IN ('EXPO', 'FCM', 'APNS', 'WEB_PUSH')",
            name="PushInstallation_transport_check",
        ),
        sa.CheckConstraint(
            '"permissionState" IS NULL OR "permissionState" IN ' "('DEFAULT', 'GRANTED', 'DENIED')",
            name="PushInstallation_permissionState_check",
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId",
            "installationId",
            "transport",
            name="PushInstallation_userId_installationId_transport_key",
        ),
    )
    op.create_index(
        "PushInstallation_userId_disabledAt_idx",
        "PushInstallation",
        ["userId", "disabledAt"],
    )
    op.create_index(
        "PushInstallation_token_key",
        "PushInstallation",
        ["token"],
        unique=True,
        postgresql_where=sa.text("token IS NOT NULL"),
    )
    op.create_index(
        "PushInstallation_endpoint_key",
        "PushInstallation",
        ["endpoint"],
        unique=True,
        postgresql_where=sa.text("endpoint IS NOT NULL"),
    )

    op.create_table(
        "NotificationDelivery",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("notificationId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("destinationId", sa.String(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PLANNED", nullable=False),
        sa.Column("eligibleAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nextAttemptAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attemptCount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("maxAttempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("providerMessageId", sa.String(), nullable=True),
        sa.Column("suppressionReason", sa.String(), nullable=True),
        sa.Column("failureCode", sa.String(), nullable=True),
        sa.Column("failureDetail", sa.Text(), nullable=True),
        sa.Column("acceptedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deliveredAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "channel IN ('IN_APP', 'MOBILE_PUSH', 'WEB_PUSH', 'EMAIL')",
            name="NotificationDelivery_channel_check",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'SUPPRESSED', 'QUEUED', 'SENDING', "
            "'ACCEPTED', 'DELIVERED', 'FAILED', 'EXPIRED', 'CANCELLED')",
            name="NotificationDelivery_status_check",
        ),
        sa.CheckConstraint(
            '"attemptCount" >= 0 AND "maxAttempts" >= 1',
            name="NotificationDelivery_attempts_check",
        ),
        sa.ForeignKeyConstraint(["notificationId"], ["Notification.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destinationId"], ["PushInstallation.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "NotificationDelivery_notificationId_channel_idx",
        "NotificationDelivery",
        ["notificationId", "channel"],
    )
    op.create_index(
        "NotificationDelivery_status_nextAttemptAt_idx",
        "NotificationDelivery",
        ["status", "nextAttemptAt"],
    )
    op.create_index(
        "NotificationDelivery_userId_createdAt_idx",
        "NotificationDelivery",
        ["userId", "createdAt"],
    )

    op.create_table(
        "NotificationDeliveryAttempt",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("deliveryId", sa.String(), nullable=False),
        sa.Column("attemptNumber", sa.Integer(), nullable=False),
        sa.Column("requestedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("durationMs", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("providerMessageId", sa.String(), nullable=True),
        sa.Column("providerReceiptId", sa.String(), nullable=True),
        sa.Column("responseMetadata", sa.JSON(), nullable=True),
        sa.Column("errorCode", sa.String(), nullable=True),
        sa.Column("errorDetail", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            '"attemptNumber" >= 1',
            name="NotificationDeliveryAttempt_attemptNumber_check",
        ),
        sa.ForeignKeyConstraint(["deliveryId"], ["NotificationDelivery.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deliveryId",
            "attemptNumber",
            name="NotificationDeliveryAttempt_deliveryId_attemptNumber_key",
        ),
    )
    op.create_index(
        "NotificationDeliveryAttempt_deliveryId_requestedAt_idx",
        "NotificationDeliveryAttempt",
        ["deliveryId", "requestedAt"],
    )

    op.create_table(
        "NotificationInteraction",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("notificationId", sa.String(), nullable=False),
        sa.Column("deliveryId", sa.String(), nullable=True),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("idempotencyId", sa.String(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=False),
        sa.Column("action", sa.JSON(), nullable=True),
        sa.Column("sourceMetadata", sa.JSON(), nullable=True),
        sa.Column("occurredAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event IN ('SEEN', 'OPENED', 'CLICKED', 'READ', 'DISMISSED', "
            "'ACTIONED', 'SNOOZED', 'DECLINED', 'UNSUBSCRIBED')",
            name="NotificationInteraction_event_check",
        ),
        sa.CheckConstraint(
            "surface IN ('WEB', 'IOS', 'ANDROID', 'EMAIL')",
            name="NotificationInteraction_surface_check",
        ),
        sa.ForeignKeyConstraint(["notificationId"], ["Notification.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deliveryId"], ["NotificationDelivery.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId",
            "idempotencyId",
            name="NotificationInteraction_userId_idempotencyId_key",
        ),
    )
    op.create_index(
        "NotificationInteraction_notificationId_occurredAt_idx",
        "NotificationInteraction",
        ["notificationId", "occurredAt"],
    )
    op.create_index(
        "NotificationInteraction_userId_occurredAt_idx",
        "NotificationInteraction",
        ["userId", "occurredAt"],
    )

    op.add_column(
        "Notification",
        sa.Column("schemaVersion", sa.Integer(), server_default="1", nullable=False),
    )
    for name, type_ in (
        ("category", sa.String()),
        ("urgency", sa.String()),
        ("action", sa.JSON()),
        ("sourceDomain", sa.String()),
        ("sourceEntityType", sa.String()),
        ("sourceEntityId", sa.String()),
        ("idempotencyKey", sa.String()),
        ("groupKey", sa.String()),
        ("eligibleAt", sa.DateTime(timezone=True)),
        ("expiresAt", sa.DateTime(timezone=True)),
        ("archivedAt", sa.DateTime(timezone=True)),
        ("intelligenceDecisionId", sa.String()),
    ):
        op.add_column("Notification", sa.Column(name, type_, nullable=True))
    op.create_foreign_key(
        "Notification_intelligenceDecisionId_fkey",
        "Notification",
        "NotificationDecision",
        ["intelligenceDecisionId"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "Notification_userId_idempotencyKey_key",
        "Notification",
        ["userId", "idempotencyKey"],
    )
    op.create_index(
        "Notification_userId_groupKey_idx",
        "Notification",
        ["userId", "groupKey"],
    )


def downgrade() -> None:
    op.drop_index("Notification_userId_groupKey_idx", table_name="Notification")
    op.drop_constraint("Notification_userId_idempotencyKey_key", "Notification", type_="unique")
    op.drop_constraint(
        "Notification_intelligenceDecisionId_fkey", "Notification", type_="foreignkey"
    )
    for name in (
        "intelligenceDecisionId",
        "archivedAt",
        "expiresAt",
        "eligibleAt",
        "groupKey",
        "idempotencyKey",
        "sourceEntityId",
        "sourceEntityType",
        "sourceDomain",
        "action",
        "urgency",
        "category",
        "schemaVersion",
    ):
        op.drop_column("Notification", name)

    op.drop_index(
        "NotificationInteraction_userId_occurredAt_idx",
        table_name="NotificationInteraction",
    )
    op.drop_index(
        "NotificationInteraction_notificationId_occurredAt_idx",
        table_name="NotificationInteraction",
    )
    op.drop_table("NotificationInteraction")
    op.drop_index(
        "NotificationDeliveryAttempt_deliveryId_requestedAt_idx",
        table_name="NotificationDeliveryAttempt",
    )
    op.drop_table("NotificationDeliveryAttempt")
    op.drop_index(
        "NotificationDelivery_userId_createdAt_idx",
        table_name="NotificationDelivery",
    )
    op.drop_index(
        "NotificationDelivery_status_nextAttemptAt_idx",
        table_name="NotificationDelivery",
    )
    op.drop_index(
        "NotificationDelivery_notificationId_channel_idx",
        table_name="NotificationDelivery",
    )
    op.drop_table("NotificationDelivery")
    op.drop_index("PushInstallation_endpoint_key", table_name="PushInstallation")
    op.drop_index("PushInstallation_token_key", table_name="PushInstallation")
    op.drop_index("PushInstallation_userId_disabledAt_idx", table_name="PushInstallation")
    op.drop_table("PushInstallation")
    op.drop_index("NotificationDecision_type_createdAt_idx", table_name="NotificationDecision")
    op.drop_index("NotificationDecision_userId_createdAt_idx", table_name="NotificationDecision")
    op.drop_table("NotificationDecision")
