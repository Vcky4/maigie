"""
Goal routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.goals import (
    GoalContributionDay,
    GoalContributionSummary,
    GoalCreate,
    GoalDetailResponse,
    GoalListResponse,
    GoalProgressUpdate,
    GoalRegeneratePlanRequest,
    GoalRegeneratePlanResponse,
    GoalResponse,
    GoalStreakSummary,
    GoalUpdate,
)
from src.models.schedule import ScheduleResponse
from src.services.user_memory_service import user_memory_service

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


@router.get("", response_model=GoalListResponse)
async def list_goals(
    current_user: CurrentUser,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status (ACTIVE, COMPLETED, ARCHIVED, CANCELLED)",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    sortBy: str = Query("createdAt", pattern="^(createdAt|updatedAt|title|targetDate)$"),
    sortOrder: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List user's goals with pagination."""
    try:
        where_clause = {"userId": current_user.id}
        if status_filter:
            where_clause["status"] = status_filter

        # Calculate skip
        skip = (page - 1) * pageSize

        # Count total matching goals
        total = await db.goal.count(where=where_clause)

        # Build order dict
        order_dict = {sortBy: sortOrder}

        # Fetch paginated goals
        goals = await db.goal.find_many(
            where=where_clause,
            order=order_dict,
            skip=skip,
            take=pageSize,
        )

        goal_responses = [
            GoalResponse(
                id=goal.id,
                userId=goal.userId,
                title=goal.title,
                description=goal.description,
                targetDate=goal.targetDate.isoformat() if goal.targetDate else None,
                status=goal.status,
                progress=goal.progress,
                courseId=getattr(goal, "courseId", None),
                topicId=getattr(goal, "topicId", None),
                createdAt=goal.createdAt.isoformat(),
                updatedAt=goal.updatedAt.isoformat(),
            )
            for goal in goals
        ]

        has_more = (skip + pageSize) < total

        return GoalListResponse(
            goals=goal_responses,
            total=total,
            page=page,
            pageSize=pageSize,
            hasMore=has_more,
        )
    except Exception as e:
        print(f"Error listing goals: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list goals",
        )


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    data: GoalCreate,
    current_user: CurrentUser,
):
    """Create a new goal."""
    try:
        goal = await db.goal.create(
            data={
                "userId": current_user.id,
                "title": data.title,
                "description": data.description,
                "targetDate": data.targetDate,
                "status": data.status,
                "courseId": data.courseId,
                "topicId": data.topicId,
            }
        )

        # Record interaction for user memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="GOAL_CREATE",
            entity_type="goal",
            entity_id=goal.id,
            importance=0.7,
        )

        return GoalResponse(
            id=goal.id,
            userId=goal.userId,
            title=goal.title,
            description=goal.description,
            targetDate=goal.targetDate.isoformat() if goal.targetDate else None,
            status=goal.status,
            progress=goal.progress,
            courseId=getattr(goal, "courseId", None),
            topicId=getattr(goal, "topicId", None),
            createdAt=goal.createdAt.isoformat(),
            updatedAt=goal.updatedAt.isoformat(),
        )
    except Exception as e:
        print(f"Error creating goal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create goal",
        )


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: str,
    current_user: CurrentUser,
):
    """Get a specific goal by ID."""
    try:
        goal = await db.goal.find_first(where={"id": goal_id, "userId": current_user.id})

        if not goal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Goal not found",
            )

        return GoalResponse(
            id=goal.id,
            userId=goal.userId,
            title=goal.title,
            description=goal.description,
            targetDate=goal.targetDate.isoformat() if goal.targetDate else None,
            status=goal.status,
            progress=goal.progress,
            courseId=getattr(goal, "courseId", None),
            topicId=getattr(goal, "topicId", None),
            createdAt=goal.createdAt.isoformat(),
            updatedAt=goal.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting goal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get goal",
        )


@router.get("/{goal_id}/detail", response_model=GoalDetailResponse)
async def get_goal_detail(
    goal_id: str,
    current_user: CurrentUser,
    contributionDays: int = Query(14, ge=1, le=90),
    scheduleDays: int = Query(21, ge=1, le=90),
):
    try:
        now = datetime.now(UTC)
        goal = await db.goal.find_first(where={"id": goal_id, "userId": current_user.id})
        if not goal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Goal not found",
            )

        streak = await db.userstreak.find_unique(where={"userId": current_user.id})
        streak_summary = GoalStreakSummary(
            currentStreak=int(getattr(streak, "currentStreak", 0) or 0) if streak else 0,
            longestStreak=int(getattr(streak, "longestStreak", 0) or 0) if streak else 0,
        )

        contribution_start = now - timedelta(days=contributionDays - 1)
        daily_map: dict[str, float] = {}
        sessions: list = []
        if getattr(goal, "topicId", None) or getattr(goal, "courseId", None):
            session_where = {
                "userId": current_user.id,
                "startTime": {"gte": contribution_start, "lte": now},
            }
            if getattr(goal, "topicId", None):
                session_where["topicId"] = goal.topicId
            elif getattr(goal, "courseId", None):
                session_where["courseId"] = goal.courseId
            sessions = await db.studysession.find_many(
                where=session_where,
                order={"startTime": "asc"},
            )

        for s in sessions:
            d = s.startTime.astimezone(UTC).date().isoformat()
            daily_map[d] = float(daily_map.get(d, 0.0) + float(s.duration or 0.0))

        daily: list[GoalContributionDay] = []
        for i in range(contributionDays):
            day = (contribution_start + timedelta(days=i)).date().isoformat()
            daily.append(GoalContributionDay(date=day, minutes=float(daily_map.get(day, 0.0))))

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

        contributions = GoalContributionSummary(
            last7DaysMinutes=float(last7_minutes),
            last30DaysMinutes=float(last30_minutes),
            daily=daily,
        )

        schedule_end = now + timedelta(days=scheduleDays)
        schedules = await db.scheduleblock.find_many(
            where={
                "userId": current_user.id,
                "goalId": goal_id,
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

        goal_resp = GoalResponse(
            id=goal.id,
            userId=goal.userId,
            title=goal.title,
            description=goal.description,
            targetDate=goal.targetDate.isoformat() if goal.targetDate else None,
            status=goal.status,
            progress=goal.progress,
            courseId=getattr(goal, "courseId", None),
            topicId=getattr(goal, "topicId", None),
            createdAt=goal.createdAt.isoformat(),
            updatedAt=goal.updatedAt.isoformat(),
        )

        return GoalDetailResponse(
            goal=goal_resp,
            streak=streak_summary,
            contributions=contributions,
            schedules=schedule_responses,
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting goal detail: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get goal detail",
        )


@router.post("/{goal_id}/regenerate-plan", response_model=GoalRegeneratePlanResponse)
async def regenerate_goal_plan_route(
    goal_id: str,
    data: GoalRegeneratePlanRequest,
    current_user: CurrentUser,
):
    from src.services.planning_service import regenerate_goal_plan

    result = await regenerate_goal_plan(
        user_id=current_user.id,
        goal_id=goal_id,
        duration_weeks=data.duration_weeks,
        request=data.request,
    )
    if result.get("status") != "success":
        msg = result.get("message") or "Failed to regenerate plan"
        if result.get("rate_limited"):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

    return GoalRegeneratePlanResponse(
        status="success",
        goal_id=goal_id,
        deleted_schedule_blocks=int(result.get("deleted_schedule_blocks") or 0),
        created_schedule_blocks=int(result.get("created_schedule_blocks") or 0),
        target_date=result.get("target_date"),
        study_tips=list(result.get("study_tips") or []),
        message=result.get("message"),
    )


@router.patch("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: str,
    data: GoalUpdate,
    current_user: CurrentUser,
):
    """Update a goal."""
    try:
        # Verify goal exists and belongs to user
        goal = await db.goal.find_first(where={"id": goal_id, "userId": current_user.id})

        if not goal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Goal not found",
            )

        # Build update data from provided fields
        update_data = {}
        if data.title is not None:
            update_data["title"] = data.title
        if data.description is not None:
            update_data["description"] = data.description
        if data.targetDate is not None:
            update_data["targetDate"] = data.targetDate
        if data.status is not None:
            update_data["status"] = data.status
        if data.progress is not None:
            update_data["progress"] = data.progress
        if data.courseId is not None:
            update_data["courseId"] = data.courseId
        if data.topicId is not None:
            update_data["topicId"] = data.topicId

        # Update the goal
        updated_goal = await db.goal.update(
            where={"id": goal_id},
            data=update_data,
        )

        # Record interaction for user memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="GOAL_UPDATE",
            entity_type="goal",
            entity_id=goal_id,
            importance=0.6,
        )

        return GoalResponse(
            id=updated_goal.id,
            userId=updated_goal.userId,
            title=updated_goal.title,
            description=updated_goal.description,
            targetDate=updated_goal.targetDate.isoformat() if updated_goal.targetDate else None,
            status=updated_goal.status,
            progress=updated_goal.progress,
            courseId=getattr(updated_goal, "courseId", None),
            topicId=getattr(updated_goal, "topicId", None),
            createdAt=updated_goal.createdAt.isoformat(),
            updatedAt=updated_goal.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating goal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update goal",
        )


@router.post("/{goal_id}/progress", response_model=GoalResponse)
async def record_progress(
    goal_id: str,
    data: GoalProgressUpdate,
    current_user: CurrentUser,
):
    """Record progress for a goal."""
    try:
        # Verify goal exists and belongs to user
        goal = await db.goal.find_first(where={"id": goal_id, "userId": current_user.id})

        if not goal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Goal not found",
            )

        # Update progress
        updated_goal = await db.goal.update(
            where={"id": goal_id},
            data={"progress": data.progress},
        )

        # If progress reaches 100%, optionally mark as completed
        if data.progress >= 100.0 and updated_goal.status == "ACTIVE":
            updated_goal = await db.goal.update(
                where={"id": goal_id},
                data={"status": "COMPLETED"},
            )

        # Record interaction for user memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="GOAL_UPDATE",
            entity_type="goal",
            entity_id=goal_id,
            metadata={"progress": data.progress},
            importance=0.5,
        )

        return GoalResponse(
            id=updated_goal.id,
            userId=updated_goal.userId,
            title=updated_goal.title,
            description=updated_goal.description,
            targetDate=updated_goal.targetDate.isoformat() if updated_goal.targetDate else None,
            status=updated_goal.status,
            progress=updated_goal.progress,
            courseId=getattr(updated_goal, "courseId", None),
            topicId=getattr(updated_goal, "topicId", None),
            createdAt=updated_goal.createdAt.isoformat(),
            updatedAt=updated_goal.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error recording progress: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record progress",
        )


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: str,
    current_user: CurrentUser,
):
    """Delete a goal."""
    try:
        # Verify goal exists and belongs to user
        goal = await db.goal.find_first(where={"id": goal_id, "userId": current_user.id})

        if not goal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Goal not found",
            )

        # Delete the goal
        await db.goal.delete(where={"id": goal_id})

        return None
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting goal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete goal",
        )
