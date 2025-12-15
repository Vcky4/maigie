"""
Course routes.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from prisma import Client as PrismaClient
from prisma.models import User

from ..dependencies import CurrentUser
from ..models.courses import (
    CourseCreate,
    CourseListItem,
    CourseListResponse,
    CourseResponse,
    CourseUpdate,
    ModuleCreate,
    ModuleResponse,
    ModuleSummary,
    ModuleUpdate,
    ProgressResponse,
    TopicCreate,
    TopicResponse,
    TopicUpdate,
)
from ..utils.dependencies import get_db_client
from ..utils.exceptions import (
    ForbiddenError,
    ResourceNotFoundError,
    SubscriptionLimitError,
    ValidationError,
)

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


# ============================================================================
# Helper Functions for Progress Calculation
# ============================================================================


async def calculate_topic_list_progress(topics: list[Any]) -> tuple[float, int, int]:
    """
    Calculate progress from a list of topics.

    Args:
        topics: List of topic objects with 'completed' field

    Returns:
        Tuple of (progress_percentage, total_topics, completed_topics)
    """
    total = len(topics)
    if total == 0:
        return 0.0, 0, 0

    completed = sum(1 for topic in topics if topic.completed)
    progress = (completed / total) * 100

    return progress, total, completed


async def calculate_module_progress(db: PrismaClient, module_id: str) -> tuple[float, bool]:
    """
    Calculate progress for a single module.

    Args:
        db: Prisma client
        module_id: Module ID

    Returns:
        Tuple of (progress_percentage, is_completed)
    """
    topics = await db.topic.find_many(where={"moduleId": module_id})

    if not topics:
        return 0.0, True  # No topics = considered complete

    progress, total, completed = await calculate_topic_list_progress(topics)
    is_completed = completed == total

    return progress, is_completed


async def calculate_course_progress(db: PrismaClient, course_id: str) -> tuple[float, int, int]:
    """
    Calculate overall course progress based on total topics.

    Formula: (Completed Topics / Total Topics in Course) × 100

    Args:
        db: Prisma client
        course_id: Course ID

    Returns:
        Tuple of (progress_percentage, total_topics, completed_topics)
    """
    # Count total topics across all modules in the course
    total_topics = await db.topic.count(where={"module": {"courseId": course_id}})

    if total_topics == 0:
        return 0.0, 0, 0

    # Count completed topics
    completed_topics = await db.topic.count(
        where={"module": {"courseId": course_id}, "completed": True}
    )

    progress = (completed_topics / total_topics) * 100

    return progress, total_topics, completed_topics


async def enrich_module_with_progress(
    db: PrismaClient, module: Any, include_topics: bool = True
) -> dict[str, Any]:
    """
    Enrich a module with calculated progress and completion status.

    Args:
        db: Prisma client
        module: Module object from database
        include_topics: Whether to include topics in response

    Returns:
        Dictionary with enriched module data
    """
    topics = await db.topic.find_many(where={"moduleId": module.id}, order={"order": "asc"})

    progress, total, completed = await calculate_topic_list_progress(topics)
    is_completed = completed == total if total > 0 else True

    return {
        "id": module.id,
        "courseId": module.courseId,
        "title": module.title,
        "order": module.order,
        "description": module.description,
        "completed": is_completed,
        "progress": progress,
        "topicCount": total,
        "completedTopicCount": completed,
        "topics": topics if include_topics else [],
        "createdAt": module.createdAt,
        "updatedAt": module.updatedAt,
    }


async def check_course_ownership(db: PrismaClient, course_id: str, user_id: str) -> Any:
    """
    Check if course exists and belongs to user.

    Args:
        db: Prisma client
        course_id: Course ID to check
        user_id: User ID to verify ownership

    Returns:
        Course object if found and owned by user

    Raises:
        ResourceNotFoundError: If course not found
        ForbiddenError: If user doesn't own the course
    """
    course = await db.course.find_unique(where={"id": course_id})

    if not course:
        raise ResourceNotFoundError("Course", course_id)

    if course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this course")

    return course


async def check_module_ownership(db: PrismaClient, module_id: str, user_id: str) -> tuple[Any, Any]:
    """
    Check if module exists and belongs to user (via course).

    Args:
        db: Prisma client
        module_id: Module ID to check
        user_id: User ID to verify ownership

    Returns:
        Tuple of (module, course) if found and owned by user

    Raises:
        ResourceNotFoundError: If module not found
        ForbiddenError: If user doesn't own the course
    """
    module = await db.module.find_unique(where={"id": module_id}, include={"course": True})

    if not module:
        raise ResourceNotFoundError("Module", module_id)

    if module.course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this module")

    return module, module.course


async def check_topic_ownership(
    db: PrismaClient, topic_id: str, user_id: str
) -> tuple[Any, Any, Any]:
    """
    Check if topic exists and belongs to user (via module > course).

    Args:
        db: Prisma client
        topic_id: Topic ID to check
        user_id: User ID to verify ownership

    Returns:
        Tuple of (topic, module, course) if found and owned by user

    Raises:
        ResourceNotFoundError: If topic not found
        ForbiddenError: If user doesn't own the course
    """
    topic = await db.topic.find_unique(
        where={"id": topic_id}, include={"module": {"include": {"course": True}}}
    )

    if not topic:
        raise ResourceNotFoundError("Topic", topic_id)

    if topic.module.course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this topic")

    return topic, topic.module, topic.module.course


# ============================================================================
# Course Endpoints
# ============================================================================


@router.get("", response_model=CourseListResponse)
async def list_courses(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    archived: bool | None = Query(None, description="Filter by archived status"),
    difficulty: str | None = Query(None, description="Filter by difficulty level"),
    isAIGenerated: bool | None = Query(None, description="Filter by AI-generated status"),
    search: str | None = Query(None, max_length=255, description="Search in title/description"),
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    sortBy: str = Query("createdAt", pattern="^(createdAt|updatedAt|title)$"),
    sortOrder: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    List all courses for the authenticated user.

    Supports filtering, searching, sorting, and pagination.
    """
    user_id = current_user.id

    # Build where clause
    where: dict[str, Any] = {"userId": user_id}

    if archived is not None:
        where["archived"] = archived

    if difficulty is not None:
        where["difficulty"] = difficulty.upper()

    if isAIGenerated is not None:
        where["isAIGenerated"] = isAIGenerated

    if search:
        where["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"description": {"contains": search, "mode": "insensitive"}},
        ]

    # Count total matching courses
    total = await db.course.count(where=where)

    # Fetch paginated courses
    skip = (page - 1) * pageSize
    order_dict = {sortBy: sortOrder}

    courses = await db.course.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order=order_dict,
        include={"modules": {"include": {"topics": True}}},
    )

    # Enrich courses with progress data
    course_items = []
    for course in courses:
        progress, total_topics, completed_topics = await calculate_course_progress(db, course.id)

        course_items.append(
            CourseListItem(
                id=course.id,
                userId=course.userId,
                title=course.title,
                description=course.description,
                difficulty=course.difficulty,
                targetDate=course.targetDate,
                isAIGenerated=course.isAIGenerated,
                archived=course.archived,
                progress=progress,
                totalTopics=total_topics,
                completedTopics=completed_topics,
                moduleCount=len(course.modules),
                createdAt=course.createdAt,
                updatedAt=course.updatedAt,
            )
        )

    has_more = (skip + pageSize) < total

    return CourseListResponse(
        courses=course_items,
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=has_more,
    )


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    course_data: CourseCreate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Create a new course.

    FREE tier users are limited to 2 AI-generated courses.
    Premium users have unlimited AI-generated courses.
    All users have unlimited manual courses.
    """
    user_id = current_user.id

    # Check subscription tier limits for AI-generated courses
    if course_data.isAIGenerated:
        # Check if user is on FREE tier
        if current_user.tier == "FREE":
            # Count existing AI-generated courses
            ai_course_count = await db.course.count(
                where={"userId": user_id, "isAIGenerated": True, "archived": False}
            )

            if ai_course_count >= 2:
                raise SubscriptionLimitError(
                    message="Free tier users are limited to 2 AI-generated courses. Upgrade to Premium for unlimited courses.",
                    detail=f"User has {ai_course_count} AI-generated courses (limit: 2)",
                )

    # Create the course
    course = await db.course.create(
        data={
            "userId": user_id,
            "title": course_data.title,
            "description": course_data.description,
            "difficulty": course_data.difficulty,
            "targetDate": course_data.targetDate,
            "isAIGenerated": course_data.isAIGenerated,
        }
    )

    # Return course with empty modules
    return CourseResponse(
        id=course.id,
        userId=course.userId,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        targetDate=course.targetDate,
        isAIGenerated=course.isAIGenerated,
        archived=course.archived,
        progress=0.0,
        totalTopics=0,
        completedTopics=0,
        modules=[],
        createdAt=course.createdAt,
        updatedAt=course.updatedAt,
    )


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Get detailed course information with all modules and topics.
    """
    user_id = current_user.id

    # Check ownership
    course = await check_course_ownership(db, course_id, user_id)

    # Fetch modules with topics
    modules = await db.module.find_many(
        where={"courseId": course_id},
        include={"topics": {"orderBy": {"order": "asc"}}},
        order={"order": "asc"},
    )

    # Enrich modules with progress
    enriched_modules = []
    for module in modules:
        enriched = await enrich_module_with_progress(db, module, include_topics=True)
        enriched_modules.append(ModuleResponse(**enriched))

    # Calculate overall course progress
    progress, total_topics, completed_topics = await calculate_course_progress(db, course_id)

    return CourseResponse(
        id=course.id,
        userId=course.userId,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        targetDate=course.targetDate,
        isAIGenerated=course.isAIGenerated,
        archived=course.archived,
        progress=progress,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        modules=enriched_modules,
        createdAt=course.createdAt,
        updatedAt=course.updatedAt,
    )


@router.put("/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: str,
    course_data: CourseUpdate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Update course metadata.

    Only title, description, difficulty, targetDate, and archived status can be updated.
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Build update data (only include non-None fields)
    update_data: dict[str, Any] = {}
    if course_data.title is not None:
        update_data["title"] = course_data.title
    if course_data.description is not None:
        update_data["description"] = course_data.description
    if course_data.difficulty is not None:
        update_data["difficulty"] = course_data.difficulty
    if course_data.targetDate is not None:
        update_data["targetDate"] = course_data.targetDate
    if course_data.archived is not None:
        update_data["archived"] = course_data.archived

    # Update course
    updated_course = await db.course.update(where={"id": course_id}, data=update_data)

    # Fetch full course data
    return await get_course(course_id, current_user, db)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Delete a course permanently.

    Cascading delete will remove all associated modules and topics.
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Delete course (cascading delete handles modules and topics)
    await db.course.delete(where={"id": course_id})

    return None


@router.post("/{course_id}/archive", response_model=CourseResponse)
async def archive_course(
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Archive a course (soft delete).
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Archive the course
    await db.course.update(where={"id": course_id}, data={"archived": True})

    # Return updated course
    return await get_course(course_id, current_user, db)


# ============================================================================
# Module Endpoints
# ============================================================================


@router.post(
    "/{course_id}/modules", response_model=ModuleResponse, status_code=status.HTTP_201_CREATED
)
async def create_module(
    course_id: str,
    module_data: ModuleCreate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Add a new module to a course.
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Create module
    module = await db.module.create(
        data={
            "courseId": course_id,
            "title": module_data.title,
            "order": module_data.order,
            "description": module_data.description,
        }
    )

    # Return enriched module
    enriched = await enrich_module_with_progress(db, module)
    return ModuleResponse(**enriched)


@router.put("/{course_id}/modules/{module_id}", response_model=ModuleResponse)
async def update_module(
    course_id: str,
    module_id: str,
    module_data: ModuleUpdate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Update a module's metadata.
    """
    user_id = current_user.id

    # Check ownership
    module, course = await check_module_ownership(db, module_id, user_id)

    # Verify module belongs to the specified course
    if module.courseId != course_id:
        raise ValidationError("Module does not belong to the specified course")

    # Build update data
    update_data: dict[str, Any] = {}
    if module_data.title is not None:
        update_data["title"] = module_data.title
    if module_data.order is not None:
        update_data["order"] = module_data.order
    if module_data.description is not None:
        update_data["description"] = module_data.description

    # Update module
    updated_module = await db.module.update(where={"id": module_id}, data=update_data)

    # Return enriched module
    enriched = await enrich_module_with_progress(db, updated_module)
    return ModuleResponse(**enriched)


@router.delete("/{course_id}/modules/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_module(
    course_id: str,
    module_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Delete a module and all its topics (cascading delete).
    """
    user_id = current_user.id

    # Check ownership
    module, course = await check_module_ownership(db, module_id, user_id)

    # Verify module belongs to the specified course
    if module.courseId != course_id:
        raise ValidationError("Module does not belong to the specified course")

    # Delete module (cascading delete handles topics)
    await db.module.delete(where={"id": module_id})

    return None


# ============================================================================
# Topic Endpoints
# ============================================================================


@router.post(
    "/{course_id}/modules/{module_id}/topics",
    response_model=TopicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_topic(
    course_id: str,
    module_id: str,
    topic_data: TopicCreate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Add a new topic to a module.
    """
    user_id = current_user.id

    # Check ownership
    module, course = await check_module_ownership(db, module_id, user_id)

    # Verify module belongs to the specified course
    if module.courseId != course_id:
        raise ValidationError("Module does not belong to the specified course")

    # Create topic
    topic = await db.topic.create(
        data={
            "moduleId": module_id,
            "title": topic_data.title,
            "order": topic_data.order,
            "content": topic_data.content,
            "estimatedHours": topic_data.estimatedHours,
        }
    )

    return TopicResponse(**topic.model_dump())


@router.put(
    "/{course_id}/modules/{module_id}/topics/{topic_id}",
    response_model=TopicResponse,
)
async def update_topic(
    course_id: str,
    module_id: str,
    topic_id: str,
    topic_data: TopicUpdate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Update a topic's data.
    """
    user_id = current_user.id

    # Check ownership
    topic, module, course = await check_topic_ownership(db, topic_id, user_id)

    # Verify topic belongs to the specified module and course
    if topic.moduleId != module_id or module.courseId != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    # Build update data
    update_data: dict[str, Any] = {}
    if topic_data.title is not None:
        update_data["title"] = topic_data.title
    if topic_data.order is not None:
        update_data["order"] = topic_data.order
    if topic_data.content is not None:
        update_data["content"] = topic_data.content
    if topic_data.estimatedHours is not None:
        update_data["estimatedHours"] = topic_data.estimatedHours
    if topic_data.completed is not None:
        update_data["completed"] = topic_data.completed

    # Update topic
    updated_topic = await db.topic.update(where={"id": topic_id}, data=update_data)

    return TopicResponse(**updated_topic.model_dump())


@router.delete(
    "/{course_id}/modules/{module_id}/topics/{topic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_topic(
    course_id: str,
    module_id: str,
    topic_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Delete a topic permanently.
    """
    user_id = current_user.id

    # Check ownership
    topic, module, course = await check_topic_ownership(db, topic_id, user_id)

    # Verify topic belongs to the specified module and course
    if topic.moduleId != module_id or module.courseId != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    # Delete topic
    await db.topic.delete(where={"id": topic_id})

    return None


@router.patch(
    "/{course_id}/modules/{module_id}/topics/{topic_id}/complete",
    response_model=TopicResponse,
)
async def toggle_topic_completion(
    course_id: str,
    module_id: str,
    topic_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    completed: bool = Query(..., description="Completion status"),
):
    """
    Mark a topic as completed or incomplete.
    """
    user_id = current_user.id

    # Check ownership
    topic, module, course = await check_topic_ownership(db, topic_id, user_id)

    # Verify topic belongs to the specified module and course
    if topic.moduleId != module_id or module.courseId != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    # Update completion status
    updated_topic = await db.topic.update(where={"id": topic_id}, data={"completed": completed})

    return TopicResponse(**updated_topic.model_dump())


# ============================================================================
# Progress & Analytics Endpoint
# ============================================================================


@router.get("/{course_id}/progress", response_model=ProgressResponse)
async def get_course_progress(
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Get detailed progress analytics for a course.

    Includes overall progress, module-by-module breakdown,
    and estimated hours tracking.
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Calculate overall progress
    overall_progress, total_topics, completed_topics = await calculate_course_progress(
        db, course_id
    )

    # Fetch all modules with topics
    modules = await db.module.find_many(
        where={"courseId": course_id},
        include={"topics": True},
        order={"order": "asc"},
    )

    # Calculate module-level progress
    module_progress_list = []
    completed_modules = 0
    total_estimated_hours = 0.0
    completed_estimated_hours = 0.0

    for module in modules:
        progress, total, completed = await calculate_topic_list_progress(module.topics)
        is_completed = completed == total if total > 0 else True

        if is_completed:
            completed_modules += 1

        # Calculate estimated hours
        for topic in module.topics:
            if topic.estimatedHours:
                total_estimated_hours += topic.estimatedHours
                if topic.completed:
                    completed_estimated_hours += topic.estimatedHours

        module_progress_list.append(
            {
                "moduleId": module.id,
                "title": module.title,
                "order": module.order,
                "progress": progress,
                "totalTopics": total,
                "completedTopics": completed,
                "completed": is_completed,
            }
        )

    return ProgressResponse(
        courseId=course_id,
        overallProgress=overall_progress,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        totalModules=len(modules),
        completedModules=completed_modules,
        totalEstimatedHours=total_estimated_hours,
        completedEstimatedHours=completed_estimated_hours,
        modules=module_progress_list,
    )
