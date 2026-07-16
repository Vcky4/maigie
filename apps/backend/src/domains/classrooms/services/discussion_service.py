"""
Discussion management — messages within a Classroom.

In the current architecture, discussions happen via the chat system
(ChatSession with isSpaceRoom=true). This service provides a
clean domain interface for classroom-scoped messaging.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory
from src.domains.intelligence.db_models import ChatMessage
from src.domains.learning_spaces.db_models import SpaceChatGroup

logger = logging.getLogger(__name__)


async def get_classroom_messages(
    *,
    classroom_id: str,
    space_id: str,
    limit: int = 50,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """Get recent messages from a Classroom discussion."""
    factory = get_session_factory()
    async with factory() as session:
        # Find the chat session linked to this classroom
        stmt = select(SpaceChatGroup).where(SpaceChatGroup.id == classroom_id)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()

        if not group or not group.chat_session_id:
            return []

        conditions = [ChatMessage.session_id == group.chat_session_id]
        if before:
            conditions.append(ChatMessage.id < before)

        msg_stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        msg_result = await session.execute(msg_stmt)
        messages = list(reversed(msg_result.scalars().all()))

    # Fetch user names via identity
    from src.domains.identity.repository import IdentityRepository
    identity_repo = IdentityRepository()

    result_list = []
    for m in messages:
        user = await identity_repo.find_by_id(m.user_id)
        result_list.append({
            "id": m.id,
            "userId": m.user_id,
            "userName": user.name if user else None,
            "content": m.content,
            "replyToId": m.reply_to_message_id,
            "createdAt": m.created_at,
        })

    return result_list
