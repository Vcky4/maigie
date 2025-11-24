"""Goal routes."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


@router.get("")
async def list_goals():
    """List goals."""
    # TODO: Implement list goals
    pass


@router.post("")
async def create_goal():
    """Create a new goal."""
    # TODO: Implement create goal
    pass


@router.patch("/{goal_id}")
async def update_goal(goal_id: str):
    """Update a goal."""
    # TODO: Implement update goal
    pass


@router.post("/{goal_id}/progress")
async def record_progress(goal_id: str):
    """Record progress for a goal."""
    # TODO: Implement record progress
    pass
