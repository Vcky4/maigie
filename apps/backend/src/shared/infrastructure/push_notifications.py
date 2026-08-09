"""Push notifications over Firebase Cloud Messaging.

Restored from the pre-migration ``services/push_notification_service`` module, which
this package had replaced with a silent ``pass``. Changes made during the restore:

* Device tokens are read and pruned through SQLAlchemy rather than the removed Prisma
  client.
* Permanently invalid tokens are **deleted** rather than flagged. The Prisma-era code
  set ``isActive = False``, but the SQLAlchemy ``DeviceToken`` model has no such
  column. An FCM token reported ``UNREGISTERED`` or ``NOT_FOUND`` is dead for good, so
  deleting is both correct and self-limiting; a flag would need a migration and would
  leave rows nothing ever reads.
* ``messaging.send_each`` is dispatched with ``asyncio.to_thread``. It performs
  blocking HTTP, and the original awaited nothing, stalling the event loop for the
  duration of a fan-out send.

Two things are missing before this can actually deliver, and neither belongs to this
module:

1. Nothing writes ``DeviceToken`` rows. There is no registration endpoint, so every
   send currently returns ``no_tokens``. That is reported honestly rather than being
   dressed up as a success.
2. Mobile is not in scope yet, which is why the registration surface does not exist.

Sends never raise. Callers include credit-purchase fulfilment and a Celery task, and a
notification failure must not roll back a purchase or fail a job that did its work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import delete, select

from src.config import get_settings
from src.domains.identity.db_models import DeviceToken
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

_firebase_app: firebase_admin.App | None = None

# FCM error codes meaning the token will never be valid again, so the row should go.
_DEAD_TOKEN_ERROR_CODES = frozenset(
    {
        "NOT_FOUND",
        "UNREGISTERED",
        "INVALID_ARGUMENT",
        "messaging/registration-token-not-registered",
        "messaging/invalid-registration-token",
    }
)


def _initialize_firebase() -> firebase_admin.App | None:
    """Initialise the Firebase Admin SDK once, or return None if unconfigured."""
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    # Another module may have initialised the default app already.
    try:
        _firebase_app = firebase_admin.get_app()
        return _firebase_app
    except ValueError:
        pass

    settings = get_settings()
    cred = None

    if settings.FIREBASE_SERVICE_ACCOUNT_PATH:
        path = Path(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
        if path.exists():
            cred = credentials.Certificate(str(path))
            logger.info("Firebase initialised from service account file")
        else:
            logger.warning(
                "Firebase service account file not found: %s",
                settings.FIREBASE_SERVICE_ACCOUNT_PATH,
            )

    if cred is None and settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        try:
            cred = credentials.Certificate(json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON))
            logger.info("Firebase initialised from service account JSON env var")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: %s", exc)

    if cred is None:
        logger.warning(
            "Firebase not configured, push notifications are disabled. "
            "Set FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_JSON."
        )
        return None

    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def get_firebase_app() -> firebase_admin.App | None:
    """Get or initialise the Firebase app."""
    return _initialize_firebase()


async def _active_tokens_for_user(user_id: str) -> list[str]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(DeviceToken.token).where(DeviceToken.user_id == user_id)
        )
        return [row[0] for row in result.all()]


async def _delete_dead_tokens(tokens: list[str]) -> None:
    if not tokens:
        return
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(DeviceToken).where(DeviceToken.token.in_(tokens)))
        await session.commit()


def _build_messages(
    tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None,
    image_url: str | None,
) -> list[messaging.Message]:
    notification = messaging.Notification(title=title, body=body, image=image_url)
    # FCM rejects non-string data values.
    payload = {str(k): str(v) for k, v in (data or {}).items()}

    return [
        messaging.Message(
            notification=notification,
            data=payload,
            token=token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(channel_id="default", priority="high"),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(title=title, body=body),
                        sound="default",
                        badge=1,
                    ),
                ),
            ),
        )
        for token in tokens
    ]


async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Send a notification to every registered device for a user.

    Returns a summary describing what happened. Never raises: the result reports
    ``skipped``, ``no_tokens`` or an ``error`` instead, because the callers are a
    payment fulfilment path and a background task.
    """
    app = get_firebase_app()
    if app is None:
        logger.warning("Firebase not initialised, skipping push notification for user %s", user_id)
        return {"sent": 0, "failed": 0, "skipped": True, "reason": "firebase_not_configured"}

    try:
        tokens = await _active_tokens_for_user(user_id)
    except Exception:
        logger.exception("Could not load device tokens for user %s", user_id)
        return {"sent": 0, "failed": 0, "error": "device_token_lookup_failed"}

    if not tokens:
        # Expected until a device-token registration endpoint exists.
        logger.info("No registered device tokens for user %s, nothing to send", user_id)
        return {"sent": 0, "failed": 0, "no_tokens": True}

    messages = _build_messages(tokens, title, body, data, image_url)

    try:
        # send_each performs blocking HTTP, so keep it off the event loop.
        response = await asyncio.to_thread(messaging.send_each, messages, app=app)
    except Exception:
        logger.exception("FCM send failed for user %s", user_id)
        return {"sent": 0, "failed": len(tokens), "error": "fcm_send_failed"}

    dead_tokens: list[str] = []
    for index, send_response in enumerate(response.responses):
        exception = getattr(send_response, "exception", None)
        if exception is None:
            continue
        error_code = getattr(exception, "code", None)
        if error_code in _DEAD_TOKEN_ERROR_CODES:
            dead_tokens.append(tokens[index])
            logger.info("Removing dead FCM token for user %s: %s", user_id, error_code)
        else:
            logger.warning("FCM send failed for user %s: %s", user_id, exception)

    try:
        await _delete_dead_tokens(dead_tokens)
    except Exception:
        logger.exception("Could not remove dead device tokens for user %s", user_id)

    result = {
        "sent": response.success_count,
        "failed": response.failure_count,
        "removed_tokens": len(dead_tokens),
    }
    logger.info("Push notification to user %s: %s", user_id, result)
    return result


async def send_push_to_user(
    user_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Alias kept for the Celery task and other existing callers."""
    return await send_push_notification(user_id, title, body, data)


async def send_push_to_multiple_users(
    user_ids: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Send the same notification to several users and aggregate the outcome."""
    total_sent = 0
    total_failed = 0

    for user_id in user_ids:
        result = await send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
        )
        total_sent += result.get("sent", 0)
        total_failed += result.get("failed", 0)

    return {"total_sent": total_sent, "total_failed": total_failed, "users": len(user_ids)}


async def send_topic_notification(
    user_id: str,
    topic: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a notification tagged with a topic for client-side routing."""
    return await send_push_notification(
        user_id=user_id,
        title=title,
        body=body,
        data={"topic": topic, **(data or {})},
    )
