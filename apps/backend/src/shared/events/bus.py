"""
In-process domain event bus.

Allows domains to communicate through events without importing each other.
Events are dispatched asynchronously within the same process.

Future: This can be backed by Redis pub/sub or a proper event store
for multi-instance deployments.

Usage:
    # Publishing (from any domain):
    from src.shared.events import emit
    await emit("topic.completed", {"user_id": "...", "topic_id": "..."})

    # Subscribing (typically in a domain's listeners module):
    from src.shared.events import listen

    @listen("topic.completed")
    async def handle_topic_completed(data: dict):
        await streak_service.record_activity(data["user_id"])
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Type for event handlers
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# Registry: event_name -> list of async handlers
_handlers: dict[str, list[EventHandler]] = defaultdict(list)


def listen(event_name: str):
    """Decorator to register an async handler for a domain event.

    Args:
        event_name: Dot-separated event name (e.g., "topic.completed").

    Example:
        @listen("user.registered")
        async def send_welcome_email(data: dict):
            ...
    """

    def decorator(func: EventHandler) -> EventHandler:
        _handlers[event_name].append(func)
        logger.debug(f"Registered handler {func.__name__} for event '{event_name}'")
        return func

    return decorator


async def emit(event_name: str, data: dict[str, Any] | None = None) -> None:
    """Emit a domain event, dispatching to all registered handlers.

    Handlers are executed concurrently. Failures in one handler do not
    affect others — they are logged and reported to Sentry.

    Args:
        event_name: Dot-separated event name.
        data: Event payload (arbitrary dict).
    """
    handlers = _handlers.get(event_name, [])
    if not handlers:
        return

    payload = data or {}
    logger.debug(f"Emitting event '{event_name}' to {len(handlers)} handler(s)")

    tasks = [_safe_dispatch(handler, event_name, payload) for handler in handlers]
    await asyncio.gather(*tasks)


async def _safe_dispatch(
    handler: EventHandler, event_name: str, data: dict[str, Any]
) -> None:
    """Execute a handler with error isolation."""
    try:
        await handler(data)
    except Exception as e:
        logger.error(
            f"Event handler '{handler.__name__}' failed for '{event_name}': {e}",
            exc_info=True,
        )
        try:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
        except ImportError:
            pass


def clear_handlers() -> None:
    """Clear all registered handlers (useful for testing)."""
    _handlers.clear()


def get_handler_count(event_name: str | None = None) -> int:
    """Get the number of registered handlers (for diagnostics)."""
    if event_name:
        return len(_handlers.get(event_name, []))
    return sum(len(h) for h in _handlers.values())
