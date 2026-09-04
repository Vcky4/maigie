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
    #: SHA-256 of the lowercased recipient address for channels with no installation row.
    #: A snapshot for correlation and audit, never a plaintext address.
    destination_ref: Mapped[str | None] = mapped_column("destinationRef", String(64), nullable=True)
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
            "NotificationDelivery_notification_channel_no_destination_key",
            "notificationId",
            "channel",
            unique=True,
            postgresql_where=text('"destinationId" IS NULL'),
        ),
        Index(
            "NotificationDelivery_status_nextAttemptAt_idx",
            "status",
            "nextAttemptAt",
        ),
        Index(
            "NotificationDelivery_channel_status_nextAttemptAt_idx",
            "channel",
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
            "surface IN ('WEB', 'IOS', 'ANDROID', 'EMAIL', 'SYSTEM')",
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


class EmailSuppression(Base, TimestampMixin):
    """An address Maigie must stop emailing, and why.

    **Address-level and global on purpose.** A hard bounce or a spam complaint is a statement
    about the mailbox, not about one category, so honouring it per-category would keep sending
    to an address the provider has already told us to leave alone — which is how a sender
    reputation is destroyed. Category-level choices live in `NotificationPreference`; this is
    the harder stop that outranks them.

    The address is stored only as a SHA-256 hash. Suppression is always checked with an address
    in hand, so the hash is sufficient to answer "may we send to this?" without the table
    becoming a list of everyone who ever bounced.

    Rows are released rather than deleted. A mailbox that was full in March may work in June,
    and keeping the history means a repeat suppression is visibly a repeat.
    """

    __tablename__ = "EmailSuppression"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    address_hash: Mapped[str] = mapped_column("addressHash", String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_event_id: Mapped[str | None] = mapped_column("providerEventId", String, nullable=True)
    #: Free-text operator note. Never the address, and never provider payload.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set when the suppression is deliberately lifted; a released row stops blocking sends.
    released_at: Mapped[datetime | None] = mapped_column(
        "releasedAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "reason IN ('HARD_BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL')",
            name="EmailSuppression_reason_check",
        ),
        # One active suppression per address. A released row does not occupy the slot, so the
        # same address can be suppressed again later and both attempts stay on the record.
        Index(
            "EmailSuppression_addressHash_active_key",
            "addressHash",
            unique=True,
            postgresql_where=text('"releasedAt" IS NULL'),
        ),
        Index("EmailSuppression_addressHash_idx", "addressHash"),
    )


class EmailProviderEvent(Base, TimestampMixin):
    """One webhook event from an email provider, recorded once.

    Providers retry webhooks, and they do not promise to deliver each event exactly once or in
    order. Without a uniqueness constraint on the provider's own event id, a retried
    `bounced` event would suppress an address twice and a re-ordered `delivered` after a
    `bounced` would overwrite a real failure with a success. The unique index is what makes
    ingestion replay-safe; the row is written in the same transaction as its effect, so
    "recorded" and "applied" cannot diverge.

    No provider payload is stored. The event type, the message id it refers to, and when it
    happened are the parts that decide anything; the rest is the provider's copy to keep.
    """

    __tablename__ = "EmailProviderEvent"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column("providerEventId", String, nullable=False)
    event_type: Mapped[str] = mapped_column("eventType", String(48), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(
        "providerMessageId", String, nullable=True
    )
    address_hash: Mapped[str | None] = mapped_column("addressHash", String(64), nullable=True)
    delivery_id: Mapped[str | None] = mapped_column(
        "deliveryId",
        String,
        ForeignKey("NotificationDelivery.id", ondelete="SET NULL"),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        "occurredAt", DateTime(timezone=True), nullable=False
    )
    #: What ingestion did with it, so an unmapped event type is visible rather than silent.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "providerEventId",
            name="EmailProviderEvent_provider_providerEventId_key",
        ),
        Index("EmailProviderEvent_providerMessageId_idx", "providerMessageId"),
        Index("EmailProviderEvent_occurredAt_idx", "occurredAt"),
    )


class OutboundMessage(Base, TimestampMixin):
    """Evidence that a non-notification message was sent, and what the provider said.

    Engagement notifications have `NotificationDelivery`; these are the messages that
    deliberately do not go through consent or the orchestrator — email verification, password
    reset, billing receipts, space invites. They are mandatory or user-commanded, so forcing them
    through an engagement policy would be wrong, but that left them with no record at all: when a
    learner says a reset code never arrived, there was nothing to check.

    Deliberately not linked to `Notification`: there is no notification, and inventing one would
    put a security email into a learner's notification centre.

    Content is never stored. Not the code, not the body, not the address — only its hash, the
    class of message, a purpose label, and the provider's own outcome. That is enough to answer
    "did we send it, when, and what did the provider say", which is the whole question.
    """

    __tablename__ = "OutboundMessage"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    #: Coarse class, used to decide retention and to keep security mail auditable separately.
    message_class: Mapped[str] = mapped_column("messageClass", String(16), nullable=False)
    #: Which message this was, e.g. `verification`, `password_reset`, `space_invite`.
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    address_hash: Mapped[str] = mapped_column("addressHash", String(64), nullable=False)
    #: Nullable because the account may not exist yet — an invite goes to an address, not a user.
    user_id: Mapped[str | None] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        "providerMessageId", String, nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column("errorCode", String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column("errorDetail", Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        "requestedAt", DateTime(timezone=True), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column("durationMs", Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "\"messageClass\" IN ('AUTH', 'SECURITY', 'BILLING', 'MEMBERSHIP', 'OPERATIONS')",
            name="OutboundMessage_messageClass_check",
        ),
        CheckConstraint(
            "status IN ('ACCEPTED', 'FAILED', 'SKIPPED')",
            name="OutboundMessage_status_check",
        ),
        Index("OutboundMessage_addressHash_createdAt_idx", "addressHash", "createdAt"),
        Index("OutboundMessage_userId_createdAt_idx", "userId", "createdAt"),
        Index("OutboundMessage_purpose_createdAt_idx", "purpose", "createdAt"),
        Index(
            "OutboundMessage_providerMessageId_idx",
            "providerMessageId",
            postgresql_where=text('"providerMessageId" IS NOT NULL'),
        ),
    )


class NotificationDigest(Base, TimestampMixin):
    """One digest run for one learner, one category, one period.

    **The unique key is what makes a digest safe to build repeatedly.** The planner runs hourly
    because a period ends at a different moment for every timezone, so most runs must do nothing
    for most learners. `(userId, category, period, periodStart)` means the second run of a period
    finds the existing row and stops, instead of sending a learner the same week twice.

    `notificationId` is set once the canonical digest notification exists, which is also what
    plans its email. A row with `itemCount = 0` is never created: a digest that says nothing
    happened teaches its reader to ignore the sender.
    """

    __tablename__ = "NotificationDigest"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    #: The settings category this digest was built for, not the database category of its items.
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Period bounds in UTC, derived from the learner's own local day or week.
    period_start: Mapped[datetime] = mapped_column(
        "periodStart", DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        "periodEnd", DateTime(timezone=True), nullable=False
    )
    notification_id: Mapped[str | None] = mapped_column(
        "notificationId",
        String,
        ForeignKey("Notification.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_count: Mapped[int] = mapped_column("itemCount", Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "userId",
            "category",
            "period",
            "periodStart",
            name="NotificationDigest_user_category_period_start_key",
        ),
        CheckConstraint("period IN ('DAILY', 'WEEKLY')", name="NotificationDigest_period_check"),
        CheckConstraint('"itemCount" >= 1', name="NotificationDigest_itemCount_check"),
        CheckConstraint(
            '"periodEnd" > "periodStart"', name="NotificationDigest_period_order_check"
        ),
        Index("NotificationDigest_userId_periodStart_idx", "userId", "periodStart"),
    )


class NotificationDigestItem(Base, TimestampMixin):
    """Membership of one notification in one digest.

    Exists so a notification cannot appear in two digests. Without it, an item created near a
    period boundary — or during a retry — would be summarised twice, and the learner would have
    no way to tell whether something happened once or twice.
    """

    __tablename__ = "NotificationDigestItem"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    digest_id: Mapped[str] = mapped_column(
        "digestId",
        String,
        ForeignKey("NotificationDigest.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_id: Mapped[str] = mapped_column(
        "notificationId",
        String,
        ForeignKey("Notification.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "digestId", "notificationId", name="NotificationDigestItem_digest_notification_key"
        ),
        # Global, not per digest: one notification belongs to at most one digest ever.
        UniqueConstraint("notificationId", name="NotificationDigestItem_notificationId_key"),
        Index("NotificationDigestItem_digestId_idx", "digestId"),
    )
