"""Push a domain event to a user's open websocket connections.

This was `pass`, so events published by background work never reached the browser. The
credit-purchase fulfilment path publishes here, which means a user whose purchase completed
saw nothing until they reloaded.

There is nothing to write beyond a delegation: the connection registry in
`src.core.websocket` already fans a payload out to every connection for a user. The one
thing this adds is the envelope shape, so publishers do not each invent their own.

Single-process only, inherited from the registry: with more than one worker the event
reaches only the worker holding that user's socket. Fanning out across workers needs a
shared broker.
"""

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


async def publish_ws_event(
    user_id: str | None = None,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> None:
    """Send an event to every open connection for a user.

    Args:
        user_id: Target user. Without one there is nobody to deliver to.
        event_type: Event name, delivered as ``type`` so clients can switch on it.
        payload: Event body, merged into the envelope.

    Never raises: publishing is a notification of work already done, and a closed socket
    must not fail the operation that produced the event.
    """
    if not user_id:
        logger.debug("publish_ws_event called without a user_id; nothing to deliver")
        return

    message: dict[str, Any] = {
        "type": event_type or "EVENT",
        "timestamp": datetime.now(UTC).isoformat(),
        **(payload or {}),
    }

    try:
        from src.core.websocket import manager

        await manager.send_to_user(user_id, message)
    except Exception:
        logger.warning(
            "Failed to publish websocket event type=%s to user=%s",
            event_type,
            user_id,
            exc_info=True,
        )
