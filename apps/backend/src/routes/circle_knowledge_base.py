"""
Circle Course-Group Link routes.

Manages linking courses to chat groups so the AI uses them as context.
Mounted at /api/v1/circles/{circle_id}/course-links.

Access control:
- OWNER, ADMIN: can link/unlink courses to groups
- ALL members: can view linked courses for a group
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.dependencies import CurrentUser, db

router = APIRouter(
    prefix="/api/v1/circles/{circle_id}/course-links",
    tags=["circle-course-links"],
)
logger = logging.getLogger(__name__)


# ─── Request Models ────────────────────────────────────────────────────────────


class CourseLinkCreate(BaseModel):
    """Link a course to a chat group."""

    chatGroupId: str
    courseId: str


# ─── Helpers ───────────────────────────────────────────────────────────────────


async def _verify_admin(circle_id: str, user_id: str):
    """Verify user is OWNER or ADMIN of the circle."""
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member or str(member.role) not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only circle owner or admin can manage course links.",
        )


async def _verify_member(circle_id: str, user_id: str):
    """Verify user is a member of the circle."""
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this circle.",
        )


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED)
async def link_course_to_group(circle_id: str, body: CourseLinkCreate, current_user: CurrentUser):
    """Link a course to a chat group. The AI will use this course as context for that group."""
    await _verify_admin(circle_id, current_user.id)

    # Verify the course belongs to this circle
    course = await db.course.find_first(where={"id": body.courseId, "circleId": circle_id})
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found in this circle.",
        )

    # Verify the chat group belongs to this circle
    group = await db.circlechatgroup.find_first(
        where={"id": body.chatGroupId, "circleId": circle_id}
    )
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat group not found in this circle.",
        )

    # Check if already linked
    existing = await db.circlegroupcourselink.find_first(
        where={"chatGroupId": body.chatGroupId, "courseId": body.courseId}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Course is already linked to this group.",
        )

    link = await db.circlegroupcourselink.create(
        data={
            "circleId": circle_id,
            "chatGroupId": body.chatGroupId,
            "courseId": body.courseId,
            "createdById": current_user.id,
        }
    )

    logger.info(
        "Linked course %s to group %s in circle %s", body.courseId, body.chatGroupId, circle_id
    )

    return {
        "id": link.id,
        "circleId": link.circleId,
        "chatGroupId": link.chatGroupId,
        "courseId": link.courseId,
        "createdAt": link.createdAt.isoformat(),
    }


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_course_from_group(circle_id: str, link_id: str, current_user: CurrentUser):
    """Remove a course-group link."""
    await _verify_admin(circle_id, current_user.id)

    existing = await db.circlegroupcourselink.find_first(
        where={"id": link_id, "circleId": circle_id}
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Link not found.",
        )

    await db.circlegroupcourselink.delete(where={"id": link_id})
    logger.info("Unlinked course link %s in circle %s", link_id, circle_id)


@router.get("/group/{chat_group_id}")
async def list_courses_for_group(circle_id: str, chat_group_id: str, current_user: CurrentUser):
    """List all courses linked to a specific chat group."""
    await _verify_member(circle_id, current_user.id)

    links = await db.circlegroupcourselink.find_many(
        where={"circleId": circle_id, "chatGroupId": chat_group_id},
        include={"course": True},
    )

    return {
        "courses": [
            {
                "linkId": link.id,
                "courseId": link.course.id,
                "title": link.course.title,
                "description": link.course.description,
                "difficulty": str(link.course.difficulty),
                "createdAt": link.createdAt.isoformat(),
            }
            for link in links
            if link.course
        ]
    }


@router.get("")
async def list_all_course_links(circle_id: str, current_user: CurrentUser):
    """List all course-group links in this circle."""
    await _verify_member(circle_id, current_user.id)

    links = await db.circlegroupcourselink.find_many(
        where={"circleId": circle_id},
        include={"course": True, "chatGroup": True},
    )

    return {
        "links": [
            {
                "id": link.id,
                "chatGroupId": link.chatGroupId,
                "chatGroupName": link.chatGroup.name if link.chatGroup else None,
                "courseId": link.courseId,
                "courseTitle": link.course.title if link.course else None,
                "createdAt": link.createdAt.isoformat(),
            }
            for link in links
        ]
    }
