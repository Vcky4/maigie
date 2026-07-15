"""
Discussion management — messages within a Classroom.

In the current architecture, discussions happen via the chat system
(ChatSession with isCircleRoom=true). This service provides a
clean domain interface for classroom-scoped messaging.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


async def get_classroom_messages(
    *,
    classroom_id: str,
    space_id: str,
    limit: int = 50,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """Get recent messages from a Classroom discussion.

    Messages are stored in ChatMessage linked to a ChatSession
    that belongs to the classroom's chat group.
    """
    # Find the chat session linked to this classroom
    group = await db.circlechatgroup.find_unique(where={"id": classroom_id})
    if not group or not group.chatSessionId:
        return []

    where: dict[str, Any] = {"sessionId": group.chatSessionId}
    if before:
        where["id"] = {"lt": before}

    messages = await db.chatmessage.find_many(
        where=where,
        order={"createdAt": "desc"},
        take=limit,
        include={"user": True},
    )

    return [
        {
            "id": m.id,
            "userId": m.userId,
            "userName": m.user.name if m.user else None,
            "content": m.content,
            "replyToId": m.replyToMessageId,
            "createdAt": m.createdAt,
        }
        for m in reversed(messages)
    ]
