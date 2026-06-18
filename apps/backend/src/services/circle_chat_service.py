"""
Circle Chat Service — manages the three types of chat for Circle courses:

1. Group Chat (isCircleRoom=true, circleId set) — shared room, via CircleChatGroup
2. Individual AI Chat (isCircleRoom=false, circleId set) — per paid-seat member, private
3. Personal Chat (isCircleRoom=false, circleId=null) — user's personal workspace, no circle context

This service handles type 2: Individual AI Chat for paid-seat circle members.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from prisma import Prisma

logger = logging.getLogger(__name__)


async def get_or_create_individual_circle_chat(
    db: Prisma,
    user_id: str,
    circle_id: str,
    course_id: str | None = None,
    topic_id: str | None = None,
) -> dict:
    """
    Get or create an individual AI chat session for a user within a circle.
    Requires the user to have a PLUS_SEAT in the circle.

    This is distinct from:
    - Group chat (which goes through CircleChatGroup)
    - Personal chat (which has no circleId)

    Returns the session dict.
    """
    # 1. Verify membership and paid seat
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this circle.",
        )

    seat_tier = str(getattr(member, "seatTier", "FREE_SEAT"))
    if seat_tier != "PLUS_SEAT":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Individual AI chat requires a Plus seat in this circle.",
        )

    # 2. Find existing session for this user + circle + resource
    where_clause: dict = {
        "userId": user_id,
        "circleId": circle_id,
        "isCircleRoom": False,
    }
    if course_id:
        where_clause["courseId"] = course_id
    if topic_id:
        where_clause["topicId"] = topic_id

    # If neither course nor topic, this is a general circle AI chat
    if not course_id and not topic_id:
        where_clause["courseId"] = None
        where_clause["topicId"] = None

    existing = await db.chatsession.find_first(
        where=where_clause,
        order={"updatedAt": "desc"},
    )

    if existing:
        return _serialize_session(existing)

    # 3. Create new individual circle chat session
    title = "Circle AI Chat"
    if topic_id:
        topic = await db.topic.find_unique(where={"id": topic_id}, include={"module": True})
        if topic:
            title = topic.title
            if not course_id and topic.module:
                course_id = topic.module.courseId
    elif course_id:
        course = await db.course.find_unique(where={"id": course_id})
        if course:
            title = course.title

    create_data: dict = {
        "userId": user_id,
        "circleId": circle_id,
        "title": title,
        "isActive": True,
        "isCircleRoom": False,
        "sessionType": "general",
    }
    if course_id:
        create_data["courseId"] = course_id
    if topic_id:
        create_data["topicId"] = topic_id

    session = await db.chatsession.create(data=create_data)
    return _serialize_session(session)


async def list_individual_circle_chats(
    db: Prisma,
    user_id: str,
    circle_id: str,
    take: int = 20,
) -> list[dict]:
    """
    List all individual AI chat sessions for a user in a circle.
    """
    sessions = await db.chatsession.find_many(
        where={
            "userId": user_id,
            "circleId": circle_id,
            "isCircleRoom": False,
        },
        order={"updatedAt": "desc"},
        take=take,
    )
    return [_serialize_session(s) for s in sessions]


async def check_circle_ai_chat_access(
    db: Prisma,
    user_id: str,
    circle_id: str,
) -> bool:
    """
    Check if a user has access to individual AI chat in a circle (PLUS_SEAT).
    Returns True if they have access, False otherwise.
    """
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        return False
    return str(getattr(member, "seatTier", "FREE_SEAT")) == "PLUS_SEAT"


def _serialize_session(session) -> dict:
    """Serialize a ChatSession DB record to a dict."""
    return {
        "id": session.id,
        "userId": session.userId,
        "circleId": getattr(session, "circleId", None),
        "title": session.title,
        "isActive": bool(session.isActive),
        "isCircleRoom": bool(session.isCircleRoom),
        "courseId": getattr(session, "courseId", None),
        "topicId": getattr(session, "topicId", None),
        "createdAt": (
            session.createdAt.isoformat()
            if hasattr(session.createdAt, "isoformat")
            else str(session.createdAt)
        ),
        "updatedAt": (
            session.updatedAt.isoformat()
            if hasattr(session.updatedAt, "isoformat")
            else str(session.updatedAt)
        ),
    }
