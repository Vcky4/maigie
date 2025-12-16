"""
Course routes.

Copyright (C) 2025 Maigie
"""

from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query
from pydantic import BaseModel

from src.dependencies import CurrentUser, DBDep
from src.schemas.courses import (
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    TopicUpdate
)
from src.services.course_service import CourseService
from src.services.ai_course import generate_course_content_task

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])

# --- AI GENERATION ENDPOINT (Your Main Task) ---

class AICourseRequest(BaseModel):
    topic: str
    difficulty: str = "Beginner"

@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_course(
    request: AICourseRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DBDep
):
    """
    Triggers the AI background worker to generate a course.
    Returns immediately with a placeholder course ID.
    Updates are sent via WebSocket.
    """
    # 1. Create a "Placeholder" Course immediately
    # We use the DB directly here or via service to get an ID
    placeholder_course = await db.course.create(
        data={
            "userId": user.id,
            "title": f"Learning {request.topic}",
            "description": "Waiting for AI generation...",
            "difficulty": request.difficulty,
            "isAIGenerated": True,
            # Initialize with 0 progress
            "progress": 0.0
        }
    )

    # 2. Hand off to Background Worker
    background_tasks.add_task(
        generate_course_content_task, 
        course_id=placeholder_course.id, 
        user_id=user.id, 
        topic_prompt=request.topic
    )

    return {
        "message": "AI generation started", 
        "courseId": placeholder_course.id,
        "status": "queued"
    }


# --- STANDARD CRUD ENDPOINTS ---

@router.get("", response_model=List[CourseResponse])
async def list_courses(
    user: CurrentUser, 
    db: DBDep, 
    archived: bool = False
):
    """List all courses for the current user."""
    service = CourseService(db)
    return await service.get_user_courses(user.id, archived)


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    course_data: CourseCreate, 
    user: CurrentUser, 
    db: DBDep
):
    """Create a new course manually."""
    service = CourseService(db)
    return await service.create_course(user.id, course_data)


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: str, 
    user: CurrentUser, 
    db: DBDep
):
    """Get full course details including modules and topics."""
    service = CourseService(db)
    return await service.get_course_details(course_id, user.id)


@router.patch("/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: str,
    data: CourseUpdate,
    user: CurrentUser,
    db: DBDep
):
    """Update course metadata (title, description, etc)."""
    # We check ownership inside the route for simplicity, or add it to service
    course = await db.course.find_first(where={"id": course_id, "userId": user.id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    updated = await db.course.update(
        where={"id": course_id},
        data={k: v for k, v in data.model_dump(exclude_unset=True).items()},
        include={"modules": {"include": {"topics": True}}}
    )
    return updated


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: str, 
    user: CurrentUser, 
    db: DBDep
):
    """Delete a course permanently."""
    course = await db.course.find_first(where={"id": course_id, "userId": user.id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    await db.course.delete(where={"id": course_id})
    return None


@router.patch("/topics/{topic_id}/complete")
async def mark_topic_complete(
    topic_id: str,
    completed: bool,
    user: CurrentUser,
    db: DBDep
):
    """
    Mark a topic as complete/incomplete.
    Triggers automatic progress recalculation for the course.
    """
    service = CourseService(db)
    return await service.toggle_topic_completion(topic_id, user.id, completed)