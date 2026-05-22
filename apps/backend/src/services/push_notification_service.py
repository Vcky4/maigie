"""
Push Notification Service (Firebase Cloud Messaging).

Handles sending push notifications to mobile devices via FCM.
Supports both individual and batch notifications.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, messaging

from src.config import get_settings
from src.core.database import db

logger = logging.getLogger(__name__)

_firebase_app: firebase_admin.App | None = None


def _initialize_firebase() -> firebase_admin.App | None:
    """Initialize Firebase Admin SDK (singleton).

    Returns the Firebase app instance or None if credentials are not configured.
    """
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    # Check if already initialized (e.g. by another module)
    try:
        _firebase_app = firebase_admin.get_app()
        return _firebase_app
    except ValueError:
        pass

    settings = get_settings()

    cred = None

    # Option 1: Service account JSON file path
    if settings.FIREBASE_SERVICE_ACCOUNT_PATH:
        path = Path(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
        if path.exists():
            cred = credentials.Certificate(str(path))
            logger.info("Firebase initialized from service account file")
        else:
            logger.warning(
                f"Firebase service account file not found: {settings.FIREBASE_SERVICE_ACCOUNT_PATH}"
            )

    # Option 2: Service account JSON content (env variable)
    if cred is None and settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        try:
            service_account_info = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
            cred = credentials.Certificate(service_account_info)
            logger.info("Firebase initialized from service account JSON env var")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")

    if cred is None:
        logger.warning(
            "Firebase not configured — push notifications disabled. "
            "Set FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_JSON."
        )
        return None

    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def get_firebase_app() -> firebase_admin.App | None:
    """Get or initialize the Firebase app."""
    return _initialize_firebase()


async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Send a push notification to all active devices for a user.

    Args:
        user_id: The user ID to send the notification to.
        title: Notification title.
        body: Notification body text.
        data: Optional data payload (key-value string pairs).
        image_url: Optional image URL for rich notifications.

    Returns:
        Dict with success/failure counts and details.
    """
    app = get_firebase_app()
    if app is None:
        logger.debug("Firebase not initialized — skipping push notification")
        return {"sent": 0, "failed": 0, "skipped": True}

    # Fetch all active device tokens for the user
    device_tokens = await db.devicetoken.find_many(where={"userId": user_id, "isActive": True})

    if not device_tokens:
        logger.debug(f"No active device tokens for user {user_id}")
        return {"sent": 0, "failed": 0, "no_tokens": True}

    tokens = [dt.token for dt in device_tokens]

    # Build the notification
    notification = messaging.Notification(
        title=title,
        body=body,
        image=image_url,
    )

    # Build the message for each token
    messages = [
        messaging.Message(
            notification=notification,
            data=data or {},
            token=token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="default",
                    priority="high",
                ),
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

    # Send batch
    response = messaging.send_each(messages, app=app)

    # Handle invalid tokens (mark as inactive)
    failed_tokens: list[str] = []
    for i, send_response in enumerate(response.responses):
        if send_response.exception is not None:
            error_code = getattr(send_response.exception, "code", None)
            # Token is invalid or unregistered — deactivate it
            if error_code in (
                "NOT_FOUND",
                "UNREGISTERED",
                "INVALID_ARGUMENT",
                "messaging/registration-token-not-registered",
                "messaging/invalid-registration-token",
            ):
                failed_tokens.append(tokens[i])
                logger.info(f"Deactivating invalid FCM token for user {user_id}: {error_code}")
            else:
                logger.warning(f"FCM send failed for user {user_id}: {send_response.exception}")

    # Deactivate invalid tokens in bulk
    if failed_tokens:
        await db.devicetoken.update_many(
            where={"token": {"in": failed_tokens}},
            data={"isActive": False},
        )

    result = {
        "sent": response.success_count,
        "failed": response.failure_count,
        "deactivated_tokens": len(failed_tokens),
    }
    logger.info(f"Push notification to user {user_id}: {result}")
    return result


async def send_push_to_multiple_users(
    user_ids: list[str],
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Send a push notification to multiple users.

    Args:
        user_ids: List of user IDs.
        title: Notification title.
        body: Notification body text.
        data: Optional data payload.
        image_url: Optional image URL.

    Returns:
        Aggregate results.
    """
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
    data: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send a notification to a user subscribed to a specific topic.

    This is a convenience wrapper that adds the topic to the data payload
    for client-side routing.

    Args:
        user_id: The user ID.
        topic: Notification topic/category (e.g., "schedule_reminder", "study_tip").
        title: Notification title.
        body: Notification body.
        data: Additional data payload.

    Returns:
        Send result dict.
    """
    payload = {"topic": topic, **(data or {})}
    return await send_push_notification(
        user_id=user_id,
        title=title,
        body=body,
        data=payload,
    )
