"""Chat session persistence helpers (generic session merge, onboarding session, etc.)."""

from __future__ import annotations

from prisma import Prisma


async def merge_generic_sessions(user_id: str, db: Prisma):
    """
    Finds all generic sessions for a user. If multiple exist (legacy),
    merges them JIT into the oldest session and deletes the duplicates.

    Excludes onboarding sessions — those are managed separately.
    """
    generic_sessions = await db.chatsession.find_many(
        where={
            "userId": user_id,
            "isCircleRoom": False,
            "sessionType": "general",
            "courseId": None,
            "topicId": None,
            "examPrepId": None,
            "noteId": None,
        },
        order={"createdAt": "asc"},
    )

    if not generic_sessions:
        return None

    if len(generic_sessions) == 1:
        return generic_sessions[0]

    master_session = generic_sessions[0]
    sessions_to_merge_from = generic_sessions[1:]

    for session in sessions_to_merge_from:
        # Move all messages to the master session
        await db.chatmessage.update_many(
            where={"sessionId": session.id}, data={"sessionId": master_session.id}
        )
        # Delete the now-empty session
        await db.chatsession.delete(where={"id": session.id})

    return master_session


async def get_or_create_onboarding_session(user_id: str, db: Prisma):
    """
    Get the user's onboarding session. Creates one if it doesn't exist.
    There should only ever be one onboarding session per user.
    """
    onboarding_session = await db.chatsession.find_first(
        where={
            "userId": user_id,
            "sessionType": "onboarding",
            "isCircleRoom": False,
        },
        order={"createdAt": "asc"},
    )

    if onboarding_session:
        return onboarding_session

    # Create a dedicated onboarding session
    onboarding_session = await db.chatsession.create(
        data={
            "userId": user_id,
            "title": "Onboarding",
            "isActive": False,
            "isCircleRoom": False,
            "sessionType": "onboarding",
        }
    )

    return onboarding_session
