"""Chat session persistence helpers (generic session merge, onboarding session, etc.)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update, delete

from src.shared.database import get_session_factory
from src.domains.intelligence.db_models import ChatSession, ChatMessage


async def merge_generic_sessions(user_id: str, db: Any = None):
    """
    Finds all generic sessions for a user. If multiple exist (legacy),
    merges them JIT into the oldest session and deletes the duplicates.

    Excludes onboarding sessions — those are managed separately.
    """
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.is_space_room == False,  # noqa: E712
                ChatSession.session_type == "general",
                ChatSession.course_id.is_(None),
                ChatSession.topic_id.is_(None),
                ChatSession.exam_prep_id.is_(None),
                ChatSession.note_id.is_(None),
            )
            .order_by(ChatSession.created_at.asc())
        )
        result = await session.execute(stmt)
        generic_sessions = list(result.scalars().all())

    if not generic_sessions:
        return None

    if len(generic_sessions) == 1:
        return generic_sessions[0]

    master_session = generic_sessions[0]
    sessions_to_merge_from = generic_sessions[1:]

    factory = get_session_factory()
    async with factory() as session:
        for s in sessions_to_merge_from:
            # Move all messages to the master session
            await session.execute(
                update(ChatMessage)
                .where(ChatMessage.session_id == s.id)
                .values(session_id=master_session.id)
            )
            # Delete the now-empty session
            await session.execute(delete(ChatSession).where(ChatSession.id == s.id))
        await session.commit()

    return master_session


async def get_or_create_onboarding_session(user_id: str, db: Any = None):
    """
    Get the user's onboarding session. Creates one if it doesn't exist.
    There should only ever be one onboarding session per user.
    """
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.session_type == "onboarding",
                ChatSession.is_space_room == False,  # noqa: E712
            )
            .order_by(ChatSession.created_at.asc())
        )
        result = await session.execute(stmt)
        onboarding_session = result.scalars().first()

    if onboarding_session:
        return onboarding_session

    # Create a dedicated onboarding session
    from src.domains.intelligence.repository import intelligence_repo
    onboarding_session = await intelligence_repo.create_chat_session({
        "userId": user_id,
        "title": "Onboarding",
        "isActive": False,
        "isSpaceRoom": False,
        "sessionType": "onboarding",
    })

    return onboarding_session
