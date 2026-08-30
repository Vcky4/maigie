"""Additive persistence for notification planning, delivery, and outcomes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    """Canonical durable in-app notification."""

    __tablename__ = "Notification"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    action_data: Mapped[dict | None] = mapped_column("actionData", JSON, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        "scheduledAt", DateTime(timezone=True), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        "readAt", DateTime(timezone=True), nullable=True
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    pushed_at: Mapped[datetime | None] = mapped_column(
        "pushedAt", DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="PENDING")
    schema_version: Mapped[int] = mapped_column(
        "schemaVersion", Integer, nullable=False, default=1, server_default="1"
    )
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    urgency: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_domain: Mapped[str | None] = mapped_column("sourceDomain", String, nullable=True)
    source_entity_type: Mapped[str | None] = mapped_column(
        "sourceEntityType", String, nullable=True
    )
    source_entity_id: Mapped[str | None] = mapped_column("sourceEntityId", String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column("idempotencyKey", String, nullable=True)
    group_key: Mapped[str | None] = mapped_column("groupKey", String, nullable=True)
    eligible_at: Mapped[datetime | None] = mapped_column(
        "eligibleAt", DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        "expiresAt", DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        "archivedAt", DateTime(timezone=True), nullable=True
    )
    intelligence_decision_id: Mapped[str | None] = mapped_column(
        "intelligenceDecisionId",
        String,
        ForeignKey("NotificationDecision.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("Notification_userId_status_idx", "userId", "status"),
        Index("Notification_scheduledAt_idx", "scheduledAt"),
        UniqueConstraint("userId", "idempotencyKey", name="Notification_userId_idempotencyKey_key"),
        Index("Notification_userId_groupKey_idx", "userId", "groupKey"),
        Index(
            "Notification_userId_createdAt_id_idx",
            "userId",
            text('"createdAt" DESC'),
            text("id DESC"),
        ),
        Index(
            "Notification_active_unread_idx",
            "userId",
            "eligibleAt",
            "expiresAt",
            postgresql_where=text(
                '"readAt" IS NULL AND "dismissedAt" IS NULL AND "archivedAt" IS NULL'
            ),
        ),
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} type={self.type}>"


class NotificationDecision(Base, TimestampMixin):
    """Auditable recommendation made before hard policy validates a plan."""

    __tablename__ = "NotificationDecision"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column("notificationType", String, nullable=False)
    policy_version: Mapped[str] = mapped_column("policyVersion", String, nullable=False)
    model_version: Mapped[str | None] = mapped_column("modelVersion", String, nullable=True)
    input_snapshot: Mapped[dict] = mapped_column("inputSnapshot", JSON, nullable=False)
    candidates: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason_codes: Mapped[list | None] = mapped_column("reasonCodes", JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    used_fallback: Mapped[bool] = mapped_column(
        "usedFallback", Boolean, nullable=False, default=False, server_default="false"
    )
    experiment_id: Mapped[str | None] = mapped_column("experimentId", String, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column("costUsd", Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column("latencyMs", Integer, nullable=True)

    __table_args__ = (
        Index("NotificationDecision_userId_createdAt_idx", "userId", "createdAt"),
        Index(
            "NotificationDecision_type_createdAt_idx",
            "notificationType",
            "createdAt",
        ),
    )


class PushInstallation(Base, TimestampMixin):
    """One addressable app/browser installation, alongside legacy DeviceToken."""

    __tablename__ = "PushInstallation"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    installation_id: Mapped[str] = mapped_column("installationId", String, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str | None] = mapped_column(String, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    p256dh_encrypted: Mapped[str | None] = mapped_column("p256dhEncrypted", Text, nullable=True)
    auth_encrypted: Mapped[str | None] = mapped_column("authEncrypted", Text, nullable=True)
    app_version: Mapped[str | None] = mapped_column("appVersion", String, nullable=True)
    device_locale: Mapped[str | None] = mapped_column("deviceLocale", String, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    permission_state: Mapped[str | None] = mapped_column(
        "permissionState", String(16), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        "lastSeenAt", DateTime(timezone=True), nullable=True
    )
    last_registered_at: Mapped[datetime | None] = mapped_column(
        "lastRegisteredAt", DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        "disabledAt", DateTime(timezone=True), nullable=True
    )
    failure_count: Mapped[int] = mapped_column(
        "failureCount", Integer, nullable=False, default=0, server_default="0"
    )
    revocation_secret_hash: Mapped[str | None] = mapped_column(
        "revocationSecretHash", String(64), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "userId",
            "installationId",
            "transport",
            name="PushInstallation_userId_installationId_transport_key",
        ),
        CheckConstraint(
            "platform IN ('IOS', 'ANDROID', 'WEB')",
            name="PushInstallation_platform_check",
        ),
        CheckConstraint(
            "transport IN ('EXPO', 'FCM', 'APNS', 'WEB_PUSH')",
            name="PushInstallation_transport_check",
        ),
        CheckConstraint(
            '"permissionState" IS NULL OR "permissionState" IN ' "('DEFAULT', 'GRANTED', 'DENIED')",
            name="PushInstallation_permissionState_check",
        ),
        Index("PushInstallation_userId_disabledAt_idx", "userId", "disabledAt"),
        Index(
            "PushInstallation_installationId_revocationSecretHash_idx",
            "installationId",
            "revocationSecretHash",
            postgresql_where=text('"revocationSecretHash" IS NOT NULL'),
        ),
        Index(
            "PushInstallation_token_key",
            "token",
            unique=True,
            postgresql_where=text("token IS NOT NULL"),
        ),
        Index(
            "PushInstallation_endpoint_key",
            "endpoint",
            unique=True,
            postgresql_where=text("endpoint IS NOT NULL"),
        ),
    )


class NotificationDelivery(Base, TimestampMixin):
    """One planned channel delivery for a durable notification."""

    __tablename__ = "NotificationDelivery"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    notification_id: Mapped[str] = mapped_column(
        "notificationId",
        String,
        ForeignKey("Notification.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[str | None] = mapped_column(
        "destinationId",
        String,
        ForeignKey("PushInstallation.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PLANNED", server_default="PLANNED"
    )
    eligible_at: Mapped[datetime] = mapped_column(
        "eligibleAt", DateTime(timezone=True), nullable=False
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        "nextAttemptAt", DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        "expiresAt", DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        "attemptCount", Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        "maxAttempts", Integer, nullable=False, default=3, server_default="3"
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        "providerMessageId", String, nullable=True
    )
    suppression_reason: Mapped[str | None] = mapped_column(
        "suppressionReason", String, nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column("failureCode", String, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column("failureDetail", Text, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        "acceptedAt", DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        "failedAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('IN_APP', 'MOBILE_PUSH', 'WEB_PUSH', 'EMAIL')",
            name="NotificationDelivery_channel_check",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'SUPPRESSED', 'QUEUED', 'SENDING', "
            "'ACCEPTED', 'DELIVERED', 'FAILED', 'EXPIRED', 'CANCELLED')",
            name="NotificationDelivery_status_check",
        ),
        CheckConstraint(
            '"attemptCount" >= 0 AND "maxAttempts" >= 1',
            name="NotificationDelivery_attempts_check",
        ),
        Index(
            "NotificationDelivery_notificationId_channel_idx",
            "notificationId",
            "channel",
        ),
        Index(
            "NotificationDelivery_notification_channel_destination_key",
            "notificationId",
            "channel",
            "destinationId",
            unique=True,
            postgresql_where=text('"destinationId" IS NOT NULL'),
        ),
        Index(
            "NotificationDelivery_status_nextAttemptAt_idx",
            "status",
            "nextAttemptAt",
        ),
        Index(
            "NotificationDelivery_userId_createdAt_idx",
            "userId",
            "createdAt",
        ),
    )


class NotificationDeliveryAttempt(Base, TimestampMixin):
    """Append-only evidence for one provider request."""

    __tablename__ = "NotificationDeliveryAttempt"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    delivery_id: Mapped[str] = mapped_column(
        "deliveryId",
        String,
        ForeignKey("NotificationDelivery.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column("attemptNumber", Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        "requestedAt", DateTime(timezone=True), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column("durationMs", Integer, nullable=True)
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        "providerMessageId", String, nullable=True
    )
    provider_receipt_id: Mapped[str | None] = mapped_column(
        "providerReceiptId", String, nullable=True
    )
    response_metadata: Mapped[dict | None] = mapped_column("responseMetadata", JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column("errorCode", String, nullable=True)
    error_detail: Mapped[str | None] = mapped_column("errorDetail", Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "deliveryId",
            "attemptNumber",
            name="NotificationDeliveryAttempt_deliveryId_attemptNumber_key",
        ),
        CheckConstraint(
            '"attemptNumber" >= 1',
            name="NotificationDeliveryAttempt_attemptNumber_check",
        ),
        Index(
            "NotificationDeliveryAttempt_deliveryId_requestedAt_idx",
            "deliveryId",
            "requestedAt",
        ),
    )


class NotificationInteraction(Base, TimestampMixin):
    """Idempotent evidence of a user response to a notification."""

    __tablename__ = "NotificationInteraction"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    notification_id: Mapped[str] = mapped_column(
        "notificationId",
        String,
        ForeignKey("Notification.id", ondelete="CASCADE"),
        nullable=False,
    )
    delivery_id: Mapped[str | None] = mapped_column(
        "deliveryId",
        String,
        ForeignKey("NotificationDelivery.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_id: Mapped[str] = mapped_column("idempotencyId", String, nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column("sourceMetadata", JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        "occurredAt", DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "userId",
            "idempotencyId",
            name="NotificationInteraction_userId_idempotencyId_key",
        ),
        CheckConstraint(
            "event IN ('SEEN', 'OPENED', 'CLICKED', 'READ', 'DISMISSED', "
            "'ACTIONED', 'SNOOZED', 'DECLINED', 'UNSUBSCRIBED')",
            name="NotificationInteraction_event_check",
        ),
        CheckConstraint(
            "surface IN ('WEB', 'IOS', 'ANDROID', 'EMAIL')",
            name="NotificationInteraction_surface_check",
        ),
        Index(
            "NotificationInteraction_notificationId_occurredAt_idx",
            "notificationId",
            "occurredAt",
        ),
        Index(
            "NotificationInteraction_userId_occurredAt_idx",
            "userId",
            "occurredAt",
        ),
    )


class NotificationPolicy(Base, TimestampMixin):
    """One shadow global policy snapshot per user.

    Legacy fields remain the runtime source of truth until the preference API and
    orchestrator cut over. A missing legacy preference row becomes fail-closed
    here rather than inventing consent for an external channel.
    """

    __tablename__ = "NotificationPolicy"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    engagement_enabled: Mapped[bool] = mapped_column(
        "engagementEnabled", Boolean, nullable=False, default=False, server_default="false"
    )
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="UTC", server_default="UTC"
    )
    timezone_source: Mapped[str | None] = mapped_column("timezoneSource", String(16), nullable=True)
    timezone_captured_at: Mapped[datetime | None] = mapped_column(
        "timezoneCapturedAt", DateTime(timezone=True), nullable=True
    )
    language: Mapped[str] = mapped_column(String, nullable=False, default="en", server_default="en")
    quiet_hours_start: Mapped[str | None] = mapped_column(
        "quietHoursStart", String(5), nullable=True
    )
    quiet_hours_end: Mapped[str | None] = mapped_column("quietHoursEnd", String(5), nullable=True)
    max_daily_notifications: Mapped[int] = mapped_column(
        "maxDailyNotifications", Integer, nullable=False, default=5, server_default="5"
    )
    digest_local_time: Mapped[str | None] = mapped_column(
        "digestLocalTime", String(5), nullable=True
    )
    digest_day_of_week: Mapped[int | None] = mapped_column(
        "digestDayOfWeek", Integer, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("userId", name="NotificationPolicy_userId_key"),
        CheckConstraint(
            "\"timezoneSource\" IS NULL OR \"timezoneSource\" IN ('DEVICE', 'MANUAL')",
            name="NotificationPolicy_timezoneSource_check",
        ),
        CheckConstraint(
            '("quietHoursStart" IS NULL) = ("quietHoursEnd" IS NULL)',
            name="NotificationPolicy_quietHours_pair_check",
        ),
        CheckConstraint(
            '"maxDailyNotifications" >= 1',
            name="NotificationPolicy_maxDailyNotifications_check",
        ),
        CheckConstraint(
            '"digestDayOfWeek" IS NULL OR "digestDayOfWeek" BETWEEN 0 AND 6',
            name="NotificationPolicy_digestDayOfWeek_check",
        ),
        CheckConstraint(
            '"digestDayOfWeek" IS NULL OR "digestLocalTime" IS NOT NULL',
            name="NotificationPolicy_digest_schedule_check",
        ),
    )


class NotificationPreference(Base, TimestampMixin):
    """Sparse type/category by channel preference override."""

    __tablename__ = "NotificationPreference"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    notification_type: Mapped[str | None] = mapped_column("notificationType", String, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    digest_period: Mapped[str | None] = mapped_column("digestPeriod", String(16), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "category IN ('SECURITY', 'ACCOUNT', 'BILLING', 'MEMBERSHIP', "
            "'SOCIAL', 'CLASSROOM', 'LEARNING', 'PROGRESS', 'SUPPORT', 'OPERATIONS')",
            name="NotificationPreference_category_check",
        ),
        CheckConstraint(
            "channel IN ('IN_APP', 'MOBILE_PUSH', 'WEB_PUSH', 'EMAIL')",
            name="NotificationPreference_channel_check",
        ),
        CheckConstraint(
            "frequency IN ('IMMEDIATE', 'DIGEST', 'OFF')",
            name="NotificationPreference_frequency_check",
        ),
        CheckConstraint(
            "enabled = (frequency <> 'OFF')",
            name="NotificationPreference_enabled_frequency_check",
        ),
        CheckConstraint(
            "(frequency = 'DIGEST' AND \"digestPeriod\" IS NOT NULL) OR "
            "(frequency <> 'DIGEST' AND \"digestPeriod\" IS NULL)",
            name="NotificationPreference_digest_check",
        ),
        CheckConstraint(
            "\"digestPeriod\" IS NULL OR \"digestPeriod\" IN ('DAILY', 'WEEKLY')",
            name="NotificationPreference_digestPeriod_check",
        ),
        CheckConstraint(
            '"notificationType" IS NULL OR length("notificationType") > 0',
            name="NotificationPreference_notificationType_check",
        ),
        Index(
            "NotificationPreference_user_category_channel_key",
            "userId",
            "category",
            "channel",
            unique=True,
            postgresql_where=text('"notificationType" IS NULL'),
        ),
        Index(
            "NotificationPreference_user_type_channel_key",
            "userId",
            "notificationType",
            "channel",
            unique=True,
            postgresql_where=text('"notificationType" IS NOT NULL'),
        ),
        Index(
            "NotificationPreference_userId_channel_category_idx",
            "userId",
            "channel",
            "category",
        ),
    )
