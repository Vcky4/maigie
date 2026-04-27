from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from src.config import settings

ACCOUNT_DELETION_WINDOW_DAYS = 90
ACCOUNT_DELETION_REMINDER_30_DAYS = 30
ACCOUNT_DELETION_REMINDER_7_DAYS = 7


def utc_now() -> datetime:
    return datetime.now(UTC)


def pending_account_deletion_payload(
    user: Any, now: datetime | None = None
) -> dict[str, Any] | None:
    now = now or utc_now()
    scheduled_for = getattr(user, "accountDeletionScheduledFor", None)
    requested_at = getattr(user, "accountDeletionRequestedAt", None)
    cancel_token = getattr(user, "accountDeletionCancelToken", None)
    if not scheduled_for or not requested_at or not cancel_token:
        return None
    if scheduled_for <= now:
        return None
    total_seconds = max(0.0, (scheduled_for - now).total_seconds())
    days_left = int(ceil(total_seconds / 86400.0))
    return {
        "requestedAt": requested_at,
        "scheduledFor": scheduled_for,
        "daysUntilDeletion": days_left,
    }


def build_account_deletion_cancel_url(token: str) -> str:
    base = (settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200").rstrip(
        "/"
    )
    return f"{base}/account-deletion/cancel?token={token}"


def account_deletion_scheduled_for(now: datetime | None = None) -> datetime:
    now = now or utc_now()
    return now + timedelta(days=ACCOUNT_DELETION_WINDOW_DAYS)
