"""
Conversation management — create, list, get, archive sessions.

A Conversation is the container for a chat between a user and Intelligence.
Messages flow through the reasoning layer for AI responses.
"""

import logging
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.intelligence.db_models import ChatMessage, ChatSession
from src.domains.intelligence.repository import intelligence_repo
from src.shared.database import get_session_factory
from src.shared.exceptions import NotFoundError

logger = logging.getLogger(__name__)


async def create_conversation(*, user_id: str, data: dict[str, Any]) -> Any:
    """Create a new conversation (ChatSession)."""
    create_data: dict[str, Any] = {
        "userId": user_id,
        "sessionType": data.get("sessionType", "general"),
        "isActive": True,
        "isSpaceRoom": data.get("isSpaceRoom", False),
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
    if data.get("spaceId"):
        create_data["spaceId"] = data["spaceId"]

    return await intelligence_repo.create_chat_session(create_data)


def conversation_filters(
    *,
    user_id: str,
    session_type: str | None = None,
    space_id: str | None = None,
) -> list:
    """The WHERE clause for a conversation listing.

    Extracted from `list_conversations` so it can be tested without a database. Mounting the
    intelligence router made space-room conversations reachable over HTTP for the first time, and the
    property that matters — **a personal listing never returns a space room, and a space listing never
    returns another space's rooms** — was previously only assertable by reading the function.

    Ask Maigie is the personal, one-to-one surface. If the `space_id is None` branch ever stops
    excluding rooms, group conversations leak into it.
    """
    conditions = [ChatSession.user_id == user_id, ChatSession.is_active == True]  # noqa: E712
    if session_type:
        conditions.append(ChatSession.session_type == session_type)
    if space_id:
        conditions.append(ChatSession.space_id == space_id)
    else:
        conditions.append(ChatSession.space_id.is_(None))
        conditions.append(ChatSession.is_space_room == False)  # noqa: E712
    return conditions


async def list_conversations(
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    session_type: str | None = None,
    space_id: str | None = None,
) -> tuple[list, int]:
    """List conversations for a user, most recently active first."""
    factory = get_session_factory()
    async with factory() as session:
        conditions = conversation_filters(
            user_id=user_id, session_type=session_type, space_id=space_id
        )

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
) -> tuple[list, int, bool, str | None]:
    """One window of a conversation's messages, oldest-first, newest window by default.

    Returns ``(messages, total, has_more, next_cursor)``. ``next_cursor`` is the id to pass back as
    ``before`` to fetch the window preceding this one, and is ``None`` exactly when ``has_more`` is
    false.

    **The cursor resolves through `created_at`, not through the id.** It previously filtered
    ``ChatMessage.id < before`` while ordering by ``created_at`` — but ids on this table are
    ``uuid4().hex[:25]``, which is random rather than monotonic, so that comparison excluded an
    arbitrary subset of the thread and had no relationship to the ordering it was paging. A learner
    scrolling back would have seen messages vanish and reappear depending on which uuids happened to
    sort low. So ``before`` is read as "the message I already have", its ``created_at`` is looked up,
    and the window is taken from strictly before that instant. The id is tie-broken on, because two
    messages in one turn can share a timestamp.
    """
    await get_conversation(session_id=session_id, user_id=user_id)

    factory = get_session_factory()
    async with factory() as session:
        count_stmt = (
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == session_id)
        )
        total = (await session.execute(count_stmt)).scalar() or 0

        conditions = [ChatMessage.session_id == session_id]
        if before:
            cursor_stmt = select(ChatMessage.created_at).where(
                ChatMessage.id == before, ChatMessage.session_id == session_id
            )
            cursor_at = (await session.execute(cursor_stmt)).scalar_one_or_none()
            if cursor_at is None:
                # The cursor names a message that is not in this conversation. Refusing is better
                # than silently serving the newest window, which would look like a successful page
                # and quietly duplicate what the client already has.
                raise NotFoundError("Message", before)
            conditions.append(
                or_(
                    ChatMessage.created_at < cursor_at,
                    and_(ChatMessage.created_at == cursor_at, ChatMessage.id < before),
                )
            )

        # One extra row, to answer `has_more` without a second count.
        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit + 1)
        )
        result = await session.execute(stmt)
        window = list(result.scalars().all())

    has_more = len(window) > limit
    window = window[:limit]
    # `window` is newest-first here, so the oldest message in it — the cursor for the next page back —
    # is the last element.
    next_cursor = window[-1].id if (has_more and window) else None

    return list(reversed(window)), total, has_more, next_cursor


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
