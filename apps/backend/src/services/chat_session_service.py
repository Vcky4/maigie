"""Chat session persistence helpers (generic session merge, etc.)."""

from __future__ import annotations

from prisma import Prisma


async def merge_generic_sessions(user_id: str, db: Prisma):
    """
    Finds all generic sessions for a user. If multiple exist (legacy),
    merges them JIT into the oldest session and deletes the duplicates.
    """
    generic_sessions = await db.chatsession.find_many(
        where={
            "userId": user_id,
            "isCircleRoom": False,
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
