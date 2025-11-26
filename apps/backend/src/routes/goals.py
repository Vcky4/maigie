"""
Goal routes.

Copyright (C) 2024 Maigie Team

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
