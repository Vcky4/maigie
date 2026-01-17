from typing import Any, List

from fastapi import HTTPException

from prisma import Prisma
from src.models.courses import CourseCreate


class CourseService:
    def __init__(self, db: Prisma):
        self.db = db

    async def get_user_courses(self, user_id: str, archived: bool = False):
        """Fetch all courses for a user."""
        return await self.db.course.find_many(
            where={"userId": user_id, "archived": archived},
            include={"modules": {"include": {"topics": True}}},
            order={"updatedAt": "desc"},
        )

    async def get_course_details(self, course_id: str, user_id: str):
        """Fetch detailed course info."""
        course = await self.db.course.find_first(
            where={"id": course_id, "userId": user_id},
            include={
                "modules": {
                    "include": {"topics": {"orderBy": {"order": "asc"}}},
                    "orderBy": {"order": "asc"},
                }
            },
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return course

    async def create_course(self, user_id: str, data: CourseCreate):
        """Create a new course in the DB."""
        course_data = {
            "userId": user_id,
            "title": data.title,
            "description": data.description,
            "difficulty": data.difficulty,  # Enum is handled by Pydantic
            "isAIGenerated": data.isAIGenerated,
            "targetDate": data.targetDate,
        }
        return await self.db.course.create(data=course_data)

    async def toggle_topic_completion(self, topic_id: str, user_id: str, completed: bool):
        """Mark a topic as complete/incomplete."""
        # 1. Verify ownership (via nested query)
        topic = await self.db.topic.find_first(
            where={"id": topic_id, "module": {"course": {"userId": user_id}}}
        )

        if not topic:
            raise HTTPException(status_code=404, detail="Topic not found or access denied")

        # 2. Update status
        return await self.db.topic.update(where={"id": topic_id}, data={"completed": completed})
