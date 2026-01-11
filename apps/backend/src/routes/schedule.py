"""
Schedule routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.schedule import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from src.services.user_memory_service import user_memory_service

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    current_user: CurrentUser,
    start_date: datetime | None = Query(
        None,
        alias="startDate",
        description="Filter schedules starting from this date",
    ),
    end_date: datetime | None = Query(
        None,
        alias="endDate",
        description="Filter schedules ending before this date",
    ),
    course_id: str | None = Query(
        None,
        alias="courseId",
        description="Filter by course ID",
    ),
    goal_id: str | None = Query(
        None,
        alias="goalId",
        description="Filter by goal ID",
    ),
):
    """List user's schedule blocks."""
    try:
        where_clause = {"userId": current_user.id}

        # Add course filter
        if course_id:
            where_clause["courseId"] = course_id

        # Add goal filter
        if goal_id:
            where_clause["goalId"] = goal_id

        schedules = await db.scheduleblock.find_many(
            where=where_clause,
            order={"startAt": "asc"},
        )

        # Filter by date range in Python (for schedules that overlap with the range)
        # A schedule overlaps if: startAt <= end_date AND endAt >= start_date
        if start_date or end_date:
            filtered_schedules = []
            for schedule in schedules:
                overlaps = True
                if start_date and schedule.endAt < start_date:
                    overlaps = False
                if end_date and schedule.startAt > end_date:
                    overlaps = False
                if overlaps:
                    filtered_schedules.append(schedule)
            schedules = filtered_schedules

        return [
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
                createdAt=schedule.createdAt.isoformat(),
                updatedAt=schedule.updatedAt.isoformat(),
            )
            for schedule in schedules
        ]
    except Exception as e:
        print(f"Error listing schedules: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list schedules",
        )


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule_block(
    data: ScheduleCreate,
    current_user: CurrentUser,
):
    """Create a new schedule block."""
    try:
        # Validate that endAt is after startAt
        if data.endAt <= data.startAt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="endAt must be after startAt",
            )

        # Validate optional foreign keys exist if provided
        if data.courseId:
            course = await db.course.find_first(
                where={"id": data.courseId, "userId": current_user.id}
            )
            if not course:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Course not found",
                )

        if data.goalId:
            goal = await db.goal.find_first(where={"id": data.goalId, "userId": current_user.id})
            if not goal:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Goal not found",
                )

        if data.topicId:
            topic = await db.topic.find_first(where={"id": data.topicId})
            if not topic:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Topic not found",
                )

        schedule = await db.scheduleblock.create(
            data={
                "userId": current_user.id,
                "title": data.title,
                "description": data.description,
                "startAt": data.startAt,
                "endAt": data.endAt,
                "recurringRule": data.recurringRule,
                "courseId": data.courseId,
                "topicId": data.topicId,
                "goalId": data.goalId,
            }
        )

        # Record interaction for user memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="SCHEDULE_CREATE",
            entity_type="schedule",
            entity_id=schedule.id,
            importance=0.7,
        )

        return ScheduleResponse(
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
            createdAt=schedule.createdAt.isoformat(),
            updatedAt=schedule.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating schedule block: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create schedule block",
        )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    current_user: CurrentUser,
):
    """Get a specific schedule block by ID."""
    try:
        schedule = await db.scheduleblock.find_first(
            where={"id": schedule_id, "userId": current_user.id}
        )

        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule block not found",
            )

        return ScheduleResponse(
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
            createdAt=schedule.createdAt.isoformat(),
            updatedAt=schedule.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting schedule block: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get schedule block",
        )


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    data: ScheduleUpdate,
    current_user: CurrentUser,
):
    """Update a schedule block."""
    try:
        # Verify schedule exists and belongs to user
        schedule = await db.scheduleblock.find_first(
            where={"id": schedule_id, "userId": current_user.id}
        )

        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule block not found",
            )

        # Build update data from provided fields
        update_data = {}
        if data.title is not None:
            update_data["title"] = data.title
        if data.description is not None:
            update_data["description"] = data.description
        if data.startAt is not None:
            update_data["startAt"] = data.startAt
        if data.endAt is not None:
            update_data["endAt"] = data.endAt
        if data.recurringRule is not None:
            update_data["recurringRule"] = data.recurringRule
        if data.courseId is not None:
            update_data["courseId"] = data.courseId
        if data.topicId is not None:
            update_data["topicId"] = data.topicId
        if data.goalId is not None:
            update_data["goalId"] = data.goalId

        # Validate endAt is after startAt if both are being updated
        final_start_at = update_data.get("startAt", schedule.startAt)
        final_end_at = update_data.get("endAt", schedule.endAt)
        if final_end_at <= final_start_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="endAt must be after startAt",
            )

        # Validate optional foreign keys exist if provided
        if data.courseId is not None:
            course = await db.course.find_first(
                where={"id": data.courseId, "userId": current_user.id}
            )
            if not course:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Course not found",
                )

        if data.goalId is not None:
            goal = await db.goal.find_first(where={"id": data.goalId, "userId": current_user.id})
            if not goal:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Goal not found",
                )

        if data.topicId is not None:
            topic = await db.topic.find_first(where={"id": data.topicId})
            if not topic:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Topic not found",
                )

        # Update the schedule
        updated_schedule = await db.scheduleblock.update(
            where={"id": schedule_id},
            data=update_data,
        )

        # Record interaction for user memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="SCHEDULE_UPDATE",
            entity_type="schedule",
            entity_id=schedule_id,
            importance=0.6,
        )

        return ScheduleResponse(
            id=updated_schedule.id,
            userId=updated_schedule.userId,
            title=updated_schedule.title,
            description=updated_schedule.description,
            startAt=updated_schedule.startAt.isoformat(),
            endAt=updated_schedule.endAt.isoformat(),
            recurringRule=updated_schedule.recurringRule,
            courseId=getattr(updated_schedule, "courseId", None),
            topicId=getattr(updated_schedule, "topicId", None),
            goalId=getattr(updated_schedule, "goalId", None),
            createdAt=updated_schedule.createdAt.isoformat(),
            updatedAt=updated_schedule.updatedAt.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating schedule block: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update schedule block",
        )


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    current_user: CurrentUser,
):
    """Delete a schedule block."""
    try:
        # Verify schedule exists and belongs to user
        schedule = await db.scheduleblock.find_first(
            where={"id": schedule_id, "userId": current_user.id}
        )

        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule block not found",
            )

        # Delete the schedule
        await db.scheduleblock.delete(where={"id": schedule_id})

        return None
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting schedule block: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete schedule block",
        )
