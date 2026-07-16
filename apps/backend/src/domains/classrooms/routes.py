"""
Classrooms domain — API routes.

Structured learning within Learning Spaces: classrooms, sessions,
discussions, and assigned courses.

Mounted at: /api/v1/classrooms
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.shared.auth import CurrentUser

from . import models
from .repository import classroom_repo
from .services import classroom_service, discussion_service, session_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["classrooms"])


# ===========================================================================
# Classrooms (within a Learning Space)
# ===========================================================================


@router.post("/spaces/{space_id}/classrooms", response_model=models.ClassroomResponse, status_code=201)
async def create_classroom(space_id: str, body: models.ClassroomCreate, current_user: CurrentUser):
    """Create a Classroom within a Learning Space (EDUCATOR+ role)."""
    result = await classroom_service.create_classroom(
        space_id=space_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_classroom_response(result, space_id)


@router.get("/spaces/{space_id}/classrooms", response_model=list[models.ClassroomResponse])
async def list_classrooms(space_id: str, current_user: CurrentUser):
    """List all Classrooms in a Learning Space."""
    classrooms = await classroom_service.list_classrooms(space_id=space_id)
    return [_to_classroom_response(c, space_id) for c in classrooms]


@router.get("/{classroom_id}", response_model=models.ClassroomResponse)
async def get_classroom(classroom_id: str, current_user: CurrentUser):
    """Get Classroom details."""
    classroom = await classroom_service.get_classroom(classroom_id=classroom_id)
    return _to_classroom_response(classroom, classroom.space_id)


@router.put("/{classroom_id}", response_model=models.ClassroomResponse)
async def update_classroom(classroom_id: str, body: models.ClassroomUpdate, current_user: CurrentUser):
    """Update Classroom settings (EDUCATOR+ role)."""
    result = await classroom_service.update_classroom(
        classroom_id=classroom_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_classroom_response(result, result.space_id)


@router.delete("/{classroom_id}", status_code=204)
async def delete_classroom(classroom_id: str, current_user: CurrentUser):
    """Delete a Classroom (ADMIN+ role)."""
    await classroom_service.delete_classroom(classroom_id=classroom_id, user_id=current_user.id)


# ===========================================================================
# Learning Sessions
# ===========================================================================


@router.post("/spaces/{space_id}/sessions", response_model=models.SessionResponse, status_code=201)
async def create_session(space_id: str, body: models.SessionCreate, current_user: CurrentUser):
    """Schedule a Learning Session (EDUCATOR+ role)."""
    session = await session_service.create_session(
        space_id=space_id, user_id=current_user.id, data=body.model_dump()
    )
    return _to_session_response(session)


@router.get("/spaces/{space_id}/sessions", response_model=list[models.SessionResponse])
async def list_sessions(
    space_id: str,
    current_user: CurrentUser,
    classroomId: str | None = Query(None),
):
    """List sessions in a Space, optionally filtered by Classroom."""
    sessions = await session_service.list_sessions(space_id=space_id, classroom_id=classroomId)
    return [_to_session_response(s) for s in sessions]


@router.get("/spaces/{space_id}/sessions/upcoming", response_model=list[models.SessionResponse])
async def upcoming_sessions(space_id: str, current_user: CurrentUser, limit: int = Query(10, ge=1, le=50)):
    """List upcoming sessions."""
    sessions = await session_service.list_upcoming(space_id=space_id, limit=limit)
    return [_to_session_response(s) for s in sessions]


@router.put("/sessions/{session_id}", response_model=models.SessionResponse)
async def update_session(session_id: str, body: models.SessionUpdate, current_user: CurrentUser):
    """Update a session (EDUCATOR+ role)."""
    session = await session_service.update_session(
        session_id=session_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_session_response(session)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, current_user: CurrentUser):
    """Delete/cancel a session."""
    await session_service.delete_session(session_id=session_id, user_id=current_user.id)


@router.get("/spaces/{space_id}/sessions/suggestions", response_model=models.SessionSuggestionResponse)
async def suggest_sessions(space_id: str, current_user: CurrentUser):
    """Get AI-suggested sessions for the Space."""
    suggestions = await session_service.suggest_sessions(space_id=space_id, user_id=current_user.id)
    return models.SessionSuggestionResponse(suggestions=suggestions)


# ===========================================================================
# Discussions
# ===========================================================================


@router.get("/{classroom_id}/messages", response_model=list[models.DiscussionMessageResponse])
async def get_discussion_messages(
    classroom_id: str,
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None),
):
    """Get recent messages from a Classroom discussion."""
    classroom = await classroom_service.get_classroom(classroom_id=classroom_id)
    messages = await discussion_service.get_classroom_messages(
        classroom_id=classroom_id, space_id=classroom.space_id, limit=limit, before=before
    )
    return messages


# ===========================================================================
# Assigned Courses
# ===========================================================================


@router.get("/spaces/{space_id}/courses")
async def list_assigned_courses(space_id: str, current_user: CurrentUser):
    """List courses assigned to this Learning Space."""
    courses = await classroom_repo.list_assigned_courses(space_id)
    return [
        {
            "id": c.id,
            "courseId": c.id,
            "courseTitle": c.title,
            "progress": c.progress or 0.0,
            "assignedAt": c.createdAt.isoformat(),
        }
        for c in courses
    ]


@router.post("/spaces/{space_id}/courses")
async def assign_course(space_id: str, body: models.AssignCourseRequest, current_user: CurrentUser):
    """Assign a course to this Learning Space."""
    await classroom_repo.assign_course(body.courseId, space_id)
    return {"status": "ok", "courseId": body.courseId, "spaceId": space_id}


@router.delete("/spaces/{space_id}/courses/{course_id}", status_code=204)
async def unassign_course(space_id: str, course_id: str, current_user: CurrentUser):
    """Remove a course from this Learning Space."""
    await classroom_repo.unassign_course(course_id)


# ===========================================================================
# Helpers
# ===========================================================================


def _to_classroom_response(group, space_id: str) -> models.ClassroomResponse:
    return models.ClassroomResponse(
        id=group.id,
        spaceId=space_id,
        name=group.name,
        description=getattr(group, "description", None),
        visibility=getattr(group, "visibility", "PUBLIC"),
        chatSessionId=getattr(group, "chatSessionId", None),
        createdAt=group.createdAt,
        updatedAt=group.updatedAt,
    )


def _to_session_response(session) -> models.SessionResponse:
    return models.SessionResponse(
        id=session.id,
        spaceId=session.space_id,
        classroomId=getattr(session, "chatGroupId", None),
        title=session.title,
        description=session.description,
        scheduledAt=session.scheduledAt,
        duration=session.duration,
        status=session.status,
        topicId=getattr(session, "topicId", None),
        goalId=getattr(session, "goalId", None),
        createdById=session.createdById,
        createdAt=session.createdAt,
        updatedAt=session.updatedAt,
    )
