"""
Routes for per-user topic progress in Circle courses and individual AI chat.

These endpoints handle:
- Marking topics complete (per-user in circle context)
- Getting user's progress for a circle course
- Getting/creating individual AI chat sessions within a circle
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from prisma import Client as PrismaClient

from src.dependencies import CurrentUser
from src.services.circle_chat_service import (
    check_circle_ai_chat_access,
    get_or_create_individual_circle_chat,
    list_individual_circle_chats,
)
from src.services.topic_progress_service import (
    get_topics_with_user_progress,
    get_user_course_progress,
    get_user_module_progress,
    get_user_topic_completed,
    mark_topic_completed,
)
from src.utils.dependencies import get_db_client

router = APIRouter(prefix="/api/v1/circles", tags=["circle-course-progress"])
logger = logging.getLogger(__name__)


# ============================================================================
# Per-User Topic Progress
# ============================================================================


class TopicCompletionRequest(BaseModel):
    completed: bool = True


@router.patch(
    "/{circle_id}/courses/{course_id}/topics/{topic_id}/progress",
    response_model=dict,
)
async def update_topic_progress(
    circle_id: str,
    course_id: str,
    topic_id: str,
    body: TopicCompletionRequest,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Mark a topic as completed/incomplete for the current user within a circle course.
    Uses UserTopicProgress (per-user tracking).
    """
    user_id = current_user.id

    # Verify circle membership
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this circle.")

    # Verify the course belongs to this circle
    course = await db.course.find_unique(where={"id": course_id})
    if not course or course.circleId != circle_id:
        raise HTTPException(status_code=404, detail="Course not found in this circle.")

    # Verify topic belongs to this course
    topic = await db.topic.find_unique(where={"id": topic_id}, include={"module": True})
    if not topic or not topic.module or topic.module.courseId != course_id:
        raise HTTPException(status_code=404, detail="Topic not found in this course.")

    result = await mark_topic_completed(db, user_id, topic_id, circle_id, body.completed)
    return result


@router.get("/{circle_id}/courses/{course_id}/progress")
async def get_circle_course_progress(
    circle_id: str,
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Get the current user's progress on a circle course.
    """
    user_id = current_user.id

    # Verify membership
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this circle.")

    # Verify course
    course = await db.course.find_unique(where={"id": course_id})
    if not course or course.circleId != circle_id:
        raise HTTPException(status_code=404, detail="Course not found in this circle.")

    progress = await get_user_course_progress(db, user_id, course_id, circle_id)
    return progress


@router.get("/{circle_id}/courses/{course_id}/modules/{module_id}/topics")
async def get_circle_module_topics(
    circle_id: str,
    course_id: str,
    module_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Get all topics in a module with the current user's completion status.
    """
    user_id = current_user.id

    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this circle.")

    topics = await get_topics_with_user_progress(db, user_id, module_id, circle_id)
    return {"topics": topics}


# ============================================================================
# Individual AI Chat (Paid Seat)
# ============================================================================


@router.post("/{circle_id}/ai-chat")
async def get_or_create_circle_ai_chat(
    circle_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    courseId: str | None = Query(None),
    topicId: str | None = Query(None),
):
    """
    Get or create an individual AI chat session within a circle.
    Requires PLUS_SEAT. Distinct from group chat and personal chat.
    """
    session = await get_or_create_individual_circle_chat(
        db,
        user_id=current_user.id,
        circle_id=circle_id,
        course_id=courseId,
        topic_id=topicId,
    )
    return session


@router.get("/{circle_id}/ai-chat/sessions")
async def list_circle_ai_chats(
    circle_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    take: int = Query(20, ge=1, le=100),
):
    """
    List the current user's individual AI chat sessions in a circle.
    """
    sessions = await list_individual_circle_chats(
        db,
        user_id=current_user.id,
        circle_id=circle_id,
        take=take,
    )
    return {"sessions": sessions}


@router.get("/{circle_id}/ai-chat/access")
async def check_ai_chat_access(
    circle_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Check if the current user has access to individual AI chat (PLUS_SEAT).
    """
    has_access = await check_circle_ai_chat_access(db, current_user.id, circle_id)
    return {"hasAccess": has_access}
