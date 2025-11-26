"""
Course routes.

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

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


@router.get("")
async def list_courses():
    """List courses."""
    # TODO: Implement list courses
    pass


@router.post("")
async def create_course():
    """Create a new course."""
    # TODO: Implement create course
    pass


@router.get("/{course_id}")
async def get_course(course_id: str):
    """Get course details."""
    # TODO: Implement get course
    pass


@router.post("/{course_id}/enroll")
async def enroll_course(course_id: str):
    """Enroll in a course."""
    # TODO: Implement enroll
    pass
