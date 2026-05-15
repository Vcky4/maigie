"""
Course routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from prisma import Client as PrismaClient

# Import the AI worker service
from src.services.ai_course import generate_course_content_task

from src.dependencies import CurrentUser
from src.models.analytics import (
    CourseProgressItem,
    UserAnalyticsResponse,
    UserProgressSummary,
)
from src.models.courses import (
    AICourseRequest,
    CourseContributionDay,
    CourseCreate,
    CourseDetailResponse,
    CourseFootprint,
    CourseListItem,
    CourseListResponse,
    CourseOutlineSatisfactionCreate,
    CourseResponse,
    CourseStreakSummary,
    CourseUpdate,
    ModuleCreate,
    ModuleProgress,
    ModuleResponse,
    ModuleUpdate,
    ProgressResponse,
    TopicCreate,
    TopicResponse,
    TopicUpdate,
)
from src.models.schedule import ScheduleResponse
from src.services.credit_service import (
    CREDIT_COSTS,
    consume_credits,
    get_credit_usage,
)
from src.services.spaced_repetition_service import ensure_review_item_for_completed_topic
from src.utils.dependencies import get_db_client
from src.utils.exceptions import (
    SubscriptionLimitError,
    ValidationError,
)
from src.utils.progress import round_progress_percent
from src.routes.courses_helpers import (
    calculate_course_progress,
    calculate_module_progress,
    calculate_topic_list_progress,
    check_course_ownership,
    check_module_ownership,
    check_topic_ownership,
    enrich_module_with_progress,
    outline_satisfaction_recorded_for_user,
    update_goal_progress_for_course,
    update_goal_progress_for_topic,
)

router = APIRouter(tags=["courses"])


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

    # 1. Check Subscription Limits (Free Tier = Max 2 Courses/month)
    if current_user.tier == "FREE":
        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
        course_count = await db.course.count(
            where={"userId": user_id, "createdAt": {"gte": thirty_days_ago}}
        )
        if course_count >= 2:
            raise SubscriptionLimitError(
                message="You can only create 2 courses per month in your current plan.",
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
        difficulty=request.difficulty.value,
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
        include={"modules": {"include": {"topics": {"include": {"notes": True}}}}},
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
    overall_progress = round_progress_percent(
        (completed_topics / total_topics * 100) if total_topics > 0 else 0.0
    )

    # Calculate average course progress
    course_progresses = []
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0:
            completed = sum(1 for t in course_topics if t.completed)
            progress = (completed / len(course_topics)) * 100
            course_progresses.append(progress)

    average_course_progress = round_progress_percent(
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
        course_progress = round_progress_percent(
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

    # Check subscription tier limits (Free Tier = Max 2 Courses/month)
    if current_user.tier == "FREE":
        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
        course_count = await db.course.count(
            where={"userId": user_id, "createdAt": {"gte": thirty_days_ago}}
        )
        if course_count >= 2:
            raise SubscriptionLimitError(
                message="You can only create 2 courses per month in your current plan.",
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
        outlineSatisfactionRecorded=False,
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
        include={"topics": {"include": {"notes": True}, "orderBy": {"order": "asc"}}},
        order={"order": "asc"},
    )

    # Enrich modules with progress
    enriched_modules = []
    for module in modules:
        enriched = await enrich_module_with_progress(db, module, include_topics=True)
        enriched_modules.append(ModuleResponse(**enriched))

    # Calculate overall course progress
    progress, total_topics, completed_topics = await calculate_course_progress(db, course_id)

    outline_recorded = await outline_satisfaction_recorded_for_user(db, user_id, course_id)

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
        outlineSatisfactionRecorded=outline_recorded,
    )


@router.post(
    "/{course_id}/outline-satisfaction",
    status_code=status.HTTP_201_CREATED,
)
async def record_course_outline_satisfaction(
    course_id: str,
    body: CourseOutlineSatisfactionCreate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """
    Record learner reaction to an AI-generated course outline (product KPI).
    """
    user_id = current_user.id
    course = await check_course_ownership(db, course_id, user_id)
    if not getattr(course, "isAIGenerated", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Outline feedback is only recorded for AI-generated courses.",
        )
    await db.courseoutlinesatisfaction.create(
        data={
            "userId": user_id,
            "courseId": course_id,
            "kind": body.kind,
            "feedback": body.feedback,
        }
    )
    return {"status": "ok"}


@router.get("/{course_id}/detail", response_model=CourseDetailResponse)
async def get_course_detail(
    course_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    contributionDays: int = Query(14, ge=1, le=90),
    scheduleDays: int = Query(21, ge=1, le=90),
):
    user_id = current_user.id

    course = await get_course(course_id, current_user, db)

    now = datetime.now(UTC)

    streak = await db.userstreak.find_unique(where={"userId": user_id})
    user_streak = CourseStreakSummary(
        currentStreak=int(getattr(streak, "currentStreak", 0) or 0) if streak else 0,
        longestStreak=int(getattr(streak, "longestStreak", 0) or 0) if streak else 0,
    )

    window_days = max(int(contributionDays), 30)
    contribution_start = now - timedelta(days=window_days - 1)
    sessions = await db.studysession.find_many(
        where={
            "userId": user_id,
            "courseId": course_id,
            "startTime": {"gte": contribution_start, "lte": now},
        },
        order={"startTime": "asc"},
    )

    daily_map: dict[str, float] = {}
    for s in sessions:
        d = s.startTime.astimezone(UTC).date().isoformat()
        daily_map[d] = float(daily_map.get(d, 0.0) + float(s.duration or 0.0))

    daily: list[CourseContributionDay] = []
    daily_start = now - timedelta(days=int(contributionDays) - 1)
    for i in range(int(contributionDays)):
        day = (daily_start + timedelta(days=i)).date().isoformat()
        daily.append(CourseContributionDay(date=day, minutes=float(daily_map.get(day, 0.0))))

    last7_start = now - timedelta(days=6)
    last30_start = now - timedelta(days=29)
    last7_minutes = 0.0
    last30_minutes = 0.0
    for s in sessions:
        st = s.startTime.astimezone(UTC)
        dur = float(s.duration or 0.0)
        if st >= last7_start:
            last7_minutes += dur
        if st >= last30_start:
            last30_minutes += dur

    footprint = CourseFootprint(
        last7DaysMinutes=float(last7_minutes),
        last30DaysMinutes=float(last30_minutes),
        daily=daily,
    )

    streak_start = now - timedelta(days=365)
    streak_sessions = await db.studysession.find_many(
        where={
            "userId": user_id,
            "courseId": course_id,
            "startTime": {"gte": streak_start, "lte": now},
        },
        order={"startTime": "asc"},
    )
    all_dates = sorted({s.startTime.astimezone(UTC).date() for s in streak_sessions})

    def _compute_streaks(dates: list) -> tuple[int, int]:
        if not dates:
            return 0, 0
        date_set = set(dates)
        longest = 1
        curr = 1
        prev = dates[0]
        for d in dates[1:]:
            if (d - timedelta(days=1)) == prev:
                curr += 1
            else:
                longest = max(longest, curr)
                curr = 1
            prev = d
        longest = max(longest, curr)

        last = dates[-1]
        current = 1
        while (last - timedelta(days=current)) in date_set:
            current += 1
        return current, longest

    course_current, course_longest = _compute_streaks(all_dates)
    course_streak = CourseStreakSummary(
        currentStreak=int(course_current),
        longestStreak=int(course_longest),
    )

    schedule_end = now + timedelta(days=scheduleDays)
    schedules = await db.scheduleblock.find_many(
        where={
            "userId": user_id,
            "courseId": course_id,
            "startAt": {"gte": now, "lte": schedule_end},
        },
        order={"startAt": "asc"},
        take=200,
    )
    schedule_responses = [
        ScheduleResponse(
            id=schedule.id,
            userId=schedule.userId,
            title=schedule.title,
            description=schedule.description,
            startAt=schedule.startAt.isoformat(),
            endAt=schedule.endAt.isoformat(),
            recurringRule=schedule.recurringRule,
            courseId=getattr(schedule, "courseId", None),
            topicId=getattr(schedule, "topicId", None),
            goalId=getattr(schedule, "goalId", None),
            reviewItemId=getattr(schedule, "reviewItemId", None),
            googleCalendarEventId=getattr(schedule, "googleCalendarEventId", None),
            googleCalendarSyncedAt=(
                schedule.googleCalendarSyncedAt.isoformat()
                if getattr(schedule, "googleCalendarSyncedAt", None)
                else None
            ),
            createdAt=schedule.createdAt.isoformat(),
            updatedAt=schedule.updatedAt.isoformat(),
        )
        for schedule in schedules
    ]

    modules_progress = [
        ModuleProgress(
            moduleId=m.id,
            title=m.title,
            order=m.order,
            progress=m.progress,
            totalTopics=m.topicCount,
            completedTopics=m.completedTopicCount,
            completed=m.completed,
        )
        for m in course.modules
    ]
    total_estimated_hours = 0.0
    completed_estimated_hours = 0.0
    for m in course.modules:
        for t in m.topics:
            h = float(t.estimatedHours or 0.0)
            total_estimated_hours += h
            if t.completed:
                completed_estimated_hours += h

    return CourseDetailResponse(
        course=course,
        userStreak=user_streak,
        courseStreak=course_streak,
        footprint=footprint,
        schedules=schedule_responses,
        completedTopics=course.completedTopics,
        totalModules=len(course.modules),
        completedModules=sum(1 for m in course.modules if m.completed),
        totalEstimatedHours=total_estimated_hours,
        completedEstimatedHours=completed_estimated_hours,
        modules=modules_progress,
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
        include={"notes": True},
    )

    return TopicResponse.model_validate(topic, from_attributes=True)


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
        where={"id": topic_id}, data=update_data, include={"notes": True}
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
        where={"id": topic_id}, data={"completed": completed}, include={"notes": True}
    )

    # Update goal progress for goals linked to this topic
    await update_goal_progress_for_topic(db, topic_id, user_id, completed)

    # Update goal progress for goals linked to this course
    await update_goal_progress_for_course(db, course_id, user_id)

    # Spaced repetition: create ReviewItem for this topic when marked complete
    await ensure_review_item_for_completed_topic(db, user_id, topic_id)

    return TopicResponse.model_validate(updated_topic, from_attributes=True)


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
