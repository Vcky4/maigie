"""
Conversation management — create, list, get, archive sessions.

A Conversation is the container for a chat between a user and Intelligence.
Messages flow through the reasoning layer for AI responses.
"""

import logging
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory
from src.shared.exceptions import NotFoundError
from src.domains.intelligence.db_models import ChatSession, ChatMessage
from src.domains.intelligence.repository import intelligence_repo

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

    return await intelligence_repo.create_chat_session(create_data)


async def list_conversations(
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    session_type: str | None = None,
    space_id: str | None = None,
) -> tuple[list, int]:
    """List conversations for a user."""
    factory = get_session_factory()
    async with factory() as session:
        conditions = [ChatSession.user_id == user_id, ChatSession.is_active == True]  # noqa: E712
        if session_type:
            conditions.append(ChatSession.session_type == session_type)
        if space_id:
            conditions.append(ChatSession.circle_id == space_id)
        else:
            conditions.append(ChatSession.circle_id.is_(None))
            conditions.append(ChatSession.is_circle_room == False)  # noqa: E712

        count_stmt = select(func.count()).select_from(ChatSession).where(*conditions)
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(ChatSession)
            .where(*conditions)
            .order_by(ChatSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        sessions_list = list(result.scalars().all())

    return sessions_list, total


async def get_conversation(*, session_id: str, user_id: str) -> Any:
    """Get a conversation. Verifies ownership."""
    session = await intelligence_repo.find_chat_session(session_id)
    if not session or session.user_id != user_id:
        raise NotFoundError("Conversation", session_id)
    return session


async def get_messages(
    *, session_id: str, user_id: str, limit: int = 50, before: str | None = None
) -> tuple[list, int]:
    """Get messages in a conversation."""
    await get_conversation(session_id=session_id, user_id=user_id)

    factory = get_session_factory()
    async with factory() as session:
        count_stmt = select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
        total = (await session.execute(count_stmt)).scalar() or 0

        conditions = [ChatMessage.session_id == session_id]
        if before:
            conditions.append(ChatMessage.id < before)

        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = list(reversed(result.scalars().all()))

    return messages, total


async def archive_conversation(*, session_id: str, user_id: str) -> None:
    """Archive (soft-delete) a conversation."""
    await get_conversation(session_id=session_id, user_id=user_id)
    await intelligence_repo.update_chat_session(session_id, {"isActive": False})


async def delete_conversation(*, session_id: str, user_id: str) -> None:
    """Permanently delete a conversation and its messages."""
    await get_conversation(session_id=session_id, user_id=user_id)
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
        await session.execute(delete(ChatSession).where(ChatSession.id == session_id))
        await session.commit()
