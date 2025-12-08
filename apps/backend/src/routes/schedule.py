"""
Schedule routes.

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

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


@router.get("")
async def get_schedule():
    """Get schedule for a date."""
    # TODO: Implement get schedule
    pass


@router.post("")
async def create_schedule_block():
    """Create a schedule block."""
    # TODO: Implement create schedule block
    pass
