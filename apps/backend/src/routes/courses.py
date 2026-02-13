"""
Course routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from prisma import Client as PrismaClient

# Import the AI worker service
from src.services.ai_course import generate_course_content_task

from ..dependencies import CurrentUser
from ..models.analytics import (
    CourseProgressItem,
    UserAnalyticsResponse,
    UserProgressSummary,
)
from ..models.courses import (
    AICourseRequest,
    CourseCreate,
    CourseListItem,
    CourseListResponse,
    CourseResponse,
    CourseUpdate,
    ModuleCreate,
    ModuleResponse,
    ModuleUpdate,
    ProgressResponse,
    TopicCreate,
    TopicResponse,
    TopicUpdate,
)
from ..services.credit_service import (
    CREDIT_COSTS,
    consume_credits,
    get_credit_usage,
)
from ..services.spaced_repetition_service import ensure_review_item_for_completed_topic
from ..utils.dependencies import get_db_client
from ..utils.exceptions import (
    ForbiddenError,
    ResourceNotFoundError,
    SubscriptionLimitError,
    ValidationError,
)

router = APIRouter(tags=["courses"])


# ============================================================================
# Helper Functions for Progress Calculation
# ============================================================================


async def calculate_topic_list_progress(topics: list[Any]) -> tuple[float, int, int]:
    """
    Calculate progress from a list of topics.
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
    """
    total_topics = await db.topic.count(where={"module": {"courseId": course_id}})

    if total_topics == 0:
        return 0.0, 0, 0

    completed_topics = await db.topic.count(
        where={"module": {"courseId": course_id}, "completed": True}
    )

    progress = (completed_topics / total_topics) * 100

    return progress, total_topics, completed_topics


async def update_goal_progress_for_course(db: PrismaClient, course_id: str, user_id: str) -> None:
    """
    Update progress for all goals linked to a course.
    """
    # Find all goals linked to this course
    goals = await db.goal.find_many(
        where={"courseId": course_id, "userId": user_id, "status": "ACTIVE"}
    )

    if not goals:
        return

    # Calculate course progress
    course_progress, _, _ = await calculate_course_progress(db, course_id)

    # Update each goal
    for goal in goals:
        await db.goal.update(
            where={"id": goal.id},
            data={"progress": course_progress},
        )

        # Auto-complete goal if progress reaches 100%
        if course_progress >= 100.0:
            await db.goal.update(
                where={"id": goal.id},
                data={"status": "COMPLETED"},
            )


async def update_goal_progress_for_topic(
    db: PrismaClient, topic_id: str, user_id: str, completed: bool
) -> None:
    """
    Update progress for all goals linked to a specific topic.
    """
    # Find all goals linked to this topic
    goals = await db.goal.find_many(
        where={"topicId": topic_id, "userId": user_id, "status": "ACTIVE"}
    )

    if not goals:
        return

    # Topic-based goals: 100% if completed, 0% if not
    progress = 100.0 if completed else 0.0

    # Update each goal
    for goal in goals:
        await db.goal.update(
            where={"id": goal.id},
            data={"progress": progress},
        )

        # Auto-complete goal if topic is completed
        if completed:
            await db.goal.update(
                where={"id": goal.id},
                data={"status": "COMPLETED"},
            )


async def enrich_module_with_progress(
    db: PrismaClient, module: Any, include_topics: bool = True
) -> dict[str, Any]:
    """
    Enrich a module with calculated progress and completion status.
    """
    topics = await db.topic.find_many(
        where={"moduleId": module.id}, include={"note": True}, order={"order": "asc"}
    )

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
    """
    topic = await db.topic.find_unique(
        where={"id": topic_id}, include={"module": {"include": {"course": True}}, "note": True}
    )

    if not topic:
        raise ResourceNotFoundError("Topic", topic_id)

    if topic.module.course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this topic")

    return topic, topic.module, topic.module.course


# ============================================================================
# AI GENERATION ENDPOINT
# ============================================================================


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_course(
    request: AICourseRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Triggers the AI background worker to generate a course.
    Returns immediately with a placeholder course ID.
    Updates are sent via WebSocket.
    """
    user_id = current_user.id

    # 1. Check Subscription Limits (Free Tier = Max 2 Courses)
    if current_user.tier == "FREE":
        course_count = await db.course.count(where={"userId": user_id, "archived": False})
        if course_count >= 2:
            raise SubscriptionLimitError(
                message="You can only create 2 courses in your current plan.",
                detail="Start a free trial to create unlimited courses.",
            )

    # 2. Check and consume credits for AI course generation
    credits_needed = CREDIT_COSTS["ai_course_generation"]
    try:
        await consume_credits(
            current_user, credits_needed, operation="ai_course_generation", db_client=db
        )
    except SubscriptionLimitError as e:
        # Re-raise with more context
        credit_usage = await get_credit_usage(current_user, db_client=db)
        raise SubscriptionLimitError(
            message=e.message,
            detail=(
                f"{e.detail} "
                f"Current usage: {credit_usage['credits_used']:,}/{credit_usage['hard_cap']:,} credits. "
                f"Period resets: {credit_usage['period_end']}"
            ),
        )

    # 3. Create "Placeholder" Course
    # Note: We force the difficulty to uppercase to match the Prisma Enum
    placeholder_course = await db.course.create(
        data={
            "userId": user_id,
            "title": f"Learning {request.topic}",
            "description": "Waiting for AI generation...",
            "difficulty": request.difficulty.upper(),
            "isAIGenerated": True,
            "progress": 0.0,
        }
    )

    # 4. Hand off to Background Worker
    background_tasks.add_task(
        generate_course_content_task,
        course_id=placeholder_course.id,
        user_id=user_id,
        topic_prompt=request.topic,
    )

    return {
        "message": "AI generation started",
        "courseId": placeholder_course.id,
        "status": "queued",
    }


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
        include={"modules": {"include": {"topics": {"include": {"note": True}}}}},
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


# ============================================================================
# User Analytics Endpoint (must be before parameterized routes)
# ============================================================================


@router.get("/analytics", response_model=UserAnalyticsResponse)
async def get_user_analytics(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Get comprehensive analytics for the current user across all courses.

    Returns overall progress summary and detailed progress for each course.
    """
    user_id = current_user.id

    # Fetch all user courses
    courses = await db.course.find_many(
        where={"userId": user_id},
        include={"modules": {"include": {"topics": True}}},
        order={"createdAt": "desc"},
    )

    # Calculate overall statistics
    total_courses = len(courses)
    active_courses = sum(1 for c in courses if not c.archived)
    archived_courses = sum(1 for c in courses if c.archived)

    total_modules = sum(len(c.modules) for c in courses)
    total_topics = sum(len(module.topics) for c in courses for module in c.modules)
    completed_topics = sum(
        1 for c in courses for module in c.modules for topic in module.topics if topic.completed
    )

    # Calculate completed modules (all topics in module are completed)
    completed_modules = 0
    for course in courses:
        for module in course.modules:
            if len(module.topics) > 0 and all(topic.completed for topic in module.topics):
                completed_modules += 1

    # Calculate completed courses (all topics in course are completed)
    completed_courses = 0
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0 and all(topic.completed for topic in course_topics):
            completed_courses += 1

    # Calculate estimated hours
    total_estimated_hours = 0.0
    completed_estimated_hours = 0.0
    for course in courses:
        for module in course.modules:
            for topic in module.topics:
                if topic.estimatedHours:
                    total_estimated_hours += topic.estimatedHours
                    if topic.completed:
                        completed_estimated_hours += topic.estimatedHours

    # Calculate overall progress (weighted by topics)
    overall_progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0

    # Calculate average course progress
    course_progresses = []
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0:
            completed = sum(1 for t in course_topics if t.completed)
            progress = (completed / len(course_topics)) * 100
            course_progresses.append(progress)

    average_course_progress = (
        sum(course_progresses) / len(course_progresses) if course_progresses else 0.0
    )

    # Build summary
    summary = UserProgressSummary(
        userId=user_id,
        totalCourses=total_courses,
        activeCourses=active_courses,
        completedCourses=completed_courses,
        archivedCourses=archived_courses,
        totalModules=total_modules,
        completedModules=completed_modules,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        overallProgress=overall_progress,
        totalEstimatedHours=total_estimated_hours,
        completedEstimatedHours=completed_estimated_hours,
        averageCourseProgress=average_course_progress,
    )

    # Build course progress items
    course_items = []
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        course_completed_topics = sum(1 for t in course_topics if t.completed)
        course_total_topics = len(course_topics)
        course_progress = (
            (course_completed_topics / course_total_topics * 100)
            if course_total_topics > 0
            else 0.0
        )

        # Count completed modules
        course_completed_modules = sum(
            1
            for module in course.modules
            if len(module.topics) > 0 and all(topic.completed for topic in module.topics)
        )

        course_items.append(
            CourseProgressItem(
                courseId=course.id,
                title=course.title,
                progress=course_progress,
                totalTopics=course_total_topics,
                completedTopics=course_completed_topics,
                totalModules=len(course.modules),
                completedModules=course_completed_modules,
                isArchived=course.archived,
                createdAt=course.createdAt.isoformat(),
            )
        )

    return UserAnalyticsResponse(
        summary=summary,
        courses=course_items,
    )


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    course_data: CourseCreate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Create a new course manually.
    """
    user_id = current_user.id

    # Check subscription tier limits (Free Tier = Max 2 Courses)
    if current_user.tier == "FREE":
        course_count = await db.course.count(where={"userId": user_id, "archived": False})
        if course_count >= 2:
            raise SubscriptionLimitError(
                message="You can only create 2 courses in your current plan.",
                detail="Start a free trial to create unlimited courses.",
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
        include={"topics": {"include": {"note": True}, "orderBy": {"order": "asc"}}},
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
    """
    user_id = current_user.id

    # Check ownership
    await check_course_ownership(db, course_id, user_id)

    # Build update data
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
    await db.course.update(where={"id": course_id}, data=update_data)

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
    Cascade: delete goals, schedule blocks linked to course/topics; notes survive (courseId/topicId set null).
    """
    from src.services.course_delete_service import delete_course_cascade

    await delete_course_cascade(db, course_id, current_user.id)
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

    if module.courseId != course_id:
        raise ValidationError("Module does not belong to the specified course")

    # Delete module
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
        },
        include={"note": True},
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
    updated_topic = await db.topic.update(
        where={"id": topic_id}, data=update_data, include={"note": True}
    )

    # Update goal progress if completion status changed
    if topic_data.completed is not None:
        await update_goal_progress_for_topic(db, topic_id, user_id, topic_data.completed)
        await update_goal_progress_for_course(db, course_id, user_id)
        # Spaced repetition: create ReviewItem for this topic when marked complete
        await ensure_review_item_for_completed_topic(db, user_id, topic_id)

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

    if topic.moduleId != module_id or module.courseId != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    # Update completion status
    updated_topic = await db.topic.update(
        where={"id": topic_id}, data={"completed": completed}, include={"note": True}
    )

    # Update goal progress for goals linked to this topic
    await update_goal_progress_for_topic(db, topic_id, user_id, completed)

    # Update goal progress for goals linked to this course
    await update_goal_progress_for_course(db, course_id, user_id)

    # Spaced repetition: create ReviewItem for this topic when marked complete
    await ensure_review_item_for_completed_topic(db, user_id, topic_id)

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
