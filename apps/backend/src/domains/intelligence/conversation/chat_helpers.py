"""Stub — implementation pending migration from routes/chat_helpers."""

import re

MAIGIE_MENTION_PATTERN = re.compile(r"@maigie\b", re.IGNORECASE)


async def _attach_topic_resources_context(*args, **kwargs):
    """Attach topic resources context to message."""
    pass  # TODO: migrate implementation


async def _extract_suggestion(*args, **kwargs):
    """Extract suggestion from AI response."""
    return None  # TODO: migrate implementation


async def _get_circle_group_for_session(*args, **kwargs):
    """Get circle group for a given session."""
    return None  # TODO: migrate implementation


async def _is_circle_member(*args, **kwargs):
    """Check if user is a circle member."""
    return False  # TODO: migrate implementation


def _map_db_role_to_client(role: str) -> str:
    """Map database role to client-facing role string."""
    return role  # TODO: migrate implementation


def _serialize_reply_preview(*args, **kwargs):
    """Serialize a reply preview for WebSocket delivery."""
    return {}  # TODO: migrate implementation


def _strip_maigie_mention(text: str) -> str:
    """Strip @maigie mention from text."""
    return MAIGIE_MENTION_PATTERN.sub("", text).strip()
