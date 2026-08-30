"""Public API contracts for the canonical notification domain."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from src.shared.schemas import CamelModel

NotificationHistoryStatus = Literal["all", "unread", "read", "dismissed", "archived"]
InteractionEvent = Literal[
    "SEEN",
    "OPENED",
    "CLICKED",
    "READ",
    "DISMISSED",
    "ACTIONED",
    "SNOOZED",
    "DECLINED",
    "UNSUBSCRIBED",
]
InteractionSurface = Literal["WEB", "IOS", "ANDROID", "EMAIL"]


class NotificationItem(CamelModel):
    id: str
    user_id: str
    type: str
    title: str
    body: str
    priority: int
    schema_version: int
    category: str | None = None
    urgency: str | None = None
    action: dict | None = None
    # Kept until every client resolves the canonical action union.
    action_data: dict | None = None
    source_domain: str | None = None
    source_entity_type: str | None = None
    source_entity_id: str | None = None
    group_key: str | None = None
    eligible_at: datetime | None = None
    expires_at: datetime | None = None
    scheduled_at: datetime
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    archived_at: datetime | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class NotificationHistoryPage(CamelModel):
    items: list[NotificationItem]
    next_cursor: str | None = None
    unread_count: int


class UnreadCountResponse(CamelModel):
    unread_count: int


class MarkAllReadResponse(CamelModel):
    updated_count: int
    unread_count: int


class NotificationInteractionCreate(CamelModel):
    idempotency_id: str = Field(min_length=1, max_length=200)
    event: InteractionEvent
    surface: InteractionSurface
    delivery_id: str | None = None
    action: dict | None = None
    source_metadata: dict | None = None
    occurred_at: datetime | None = None


class NotificationInteractionResponse(CamelModel):
    id: str
    notification_id: str
    delivery_id: str | None = None
    user_id: str
    idempotency_id: str
    event: str
    surface: str
    action: dict | None = None
    source_metadata: dict | None = None
    occurred_at: datetime
    created_at: datetime


MobilePlatform = Literal["IOS", "ANDROID"]
PermissionState = Literal["DEFAULT", "GRANTED", "DENIED"]


class MobilePushInstallationUpsert(CamelModel):
    installation_id: str = Field(min_length=1, max_length=200)
    platform: MobilePlatform
    token: str = Field(min_length=20, max_length=512)
    app_version: str | None = Field(default=None, max_length=100)
    device_locale: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    permission_state: PermissionState = "DEFAULT"

    @field_validator("token")
    @classmethod
    def validate_expo_token(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"(?:ExponentPushToken|ExpoPushToken)\[[^\[\]]{1,400}\]", value):
            raise ValueError("token must be a valid Expo push token")
        return value


class PushInstallationResponse(CamelModel):
    id: str
    installation_id: str
    platform: str
    transport: str
    app_version: str | None = None
    device_locale: str | None = None
    timezone: str | None = None
    permission_state: str | None = None
    last_seen_at: datetime | None = None
    last_registered_at: datetime | None = None
    disabled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revocation_secret: str | None = None


class PushInstallationList(CamelModel):
    items: list[PushInstallationResponse]


class PushInstallationRevoke(CamelModel):
    installation_id: str = Field(min_length=1, max_length=200)
    revocation_secret: str = Field(min_length=32, max_length=200)
