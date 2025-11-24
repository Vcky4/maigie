"""Course routes."""

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
