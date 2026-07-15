"""
Conversation management — create, list, get, archive sessions.

A Conversation is the container for a chat between a user and Intelligence.
Messages flow through the reasoning layer for AI responses.
"""

import logging
from typing import Any

from src.shared.database import db
from src.shared.exceptions import NotFoundError

logger = logging.getLogger(__name__)


async def create_conversation(*, user_id: str, data: dict[str, Any]) -> Any:
    """Create a new conversation (ChatSession)."""
    create_data: dict[str, Any] = {
        "userId": user_id,
        "sessionType": data.get("sessionType", "general"),
        "isActive": True,
        "isCircleRoom": data.get("isCircleRoom", False),
    }
    if data.get("title"):
        create_data["title"] = data["title"]
    if data.get("courseId"):
        create_data["courseId"] = data["courseId"]
    if data.get("topicId"):
        create_data["topicId"] = data["topicId"]
    if data.get("examPrepId"):
        create_data["examPrepId"] = data["examPrepId"]
    if data.get("noteId"):
        create_data["noteId"] = data["noteId"]
    if data.get("circleId"):
        create_data["circleId"] = data["circleId"]

    return await db.chatsession.create(data=create_data)


async def list_conversations(
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    session_type: str | None = None,
    circle_id: str | None = None,
) -> tuple[list, int]:
    """List conversations for a user."""
    where: dict[str, Any] = {"userId": user_id, "isActive": True}
    if session_type:
        where["sessionType"] = session_type
    if circle_id:
        where["circleId"] = circle_id
    else:
        where["circleId"] = None
        where["isCircleRoom"] = False

    total = await db.chatsession.count(where=where)
    sessions = await db.chatsession.find_many(
        where=where,
        order={"updatedAt": "desc"},
        skip=(page - 1) * page_size,
        take=page_size,
    )
    return sessions, total


async def get_conversation(*, session_id: str, user_id: str) -> Any:
    """Get a conversation. Verifies ownership."""
    session = await db.chatsession.find_first(
        where={"id": session_id, "userId": user_id}
    )
    if not session:
        raise NotFoundError("Conversation", session_id)
    return session


async def get_messages(
    *, session_id: str, user_id: str, limit: int = 50, before: str | None = None
) -> tuple[list, int]:
    """Get messages in a conversation."""
    await get_conversation(session_id=session_id, user_id=user_id)

    where: dict[str, Any] = {"sessionId": session_id}
    if before:
        where["id"] = {"lt": before}

    total = await db.chatmessage.count(where={"sessionId": session_id})
    messages = await db.chatmessage.find_many(
        where=where,
        order={"createdAt": "desc"},
        take=limit,
    )
    return list(reversed(messages)), total


async def archive_conversation(*, session_id: str, user_id: str) -> None:
    """Archive (soft-delete) a conversation."""
    session = await get_conversation(session_id=session_id, user_id=user_id)
    await db.chatsession.update(where={"id": session_id}, data={"isActive": False})


async def delete_conversation(*, session_id: str, user_id: str) -> None:
    """Permanently delete a conversation and its messages."""
    await get_conversation(session_id=session_id, user_id=user_id)
    await db.chatmessage.delete_many(where={"sessionId": session_id})
    await db.chatsession.delete(where={"id": session_id})
