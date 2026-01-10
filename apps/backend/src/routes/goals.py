"""
Goal routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from fastapi import APIRouter, HTTPException, Query, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.goals import GoalCreate, GoalProgressUpdate, GoalResponse, GoalUpdate
from src.services.user_memory_service import user_memory_service

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


@router.get("", response_model=list[GoalResponse])
async def list_goals(
    current_user: CurrentUser,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status (ACTIVE, COMPLETED, ARCHIVED, CANCELLED)",
    ),
):
    """List user's goals."""
    try:
        where_clause = {"userId": current_user.id}
        if status_filter:
            where_clause["status"] = status_filter

        goals = await db.goal.find_many(
            where=where_clause,
            order={"createdAt": "desc"},
        )

        return [
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
