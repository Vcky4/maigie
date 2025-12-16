from typing import List
from prisma import Prisma
from fastapi import HTTPException
from src.schemas.courses import CourseCreate

class CourseService:
    def __init__(self, db: Prisma):
        self.db = db

    async def get_user_courses(self, user_id: str, archived: bool = False):
        return await self.db.course.find_many(
            where={"userId": user_id, "archived": archived},
            include={"modules": {"include": {"topics": True}}},
            order={"updatedAt": "desc"}
        )

    async def get_course_details(self, course_id: str, user_id: str):
        course = await self.db.course.find_first(
            where={"id": course_id, "userId": user_id},
            include={
                "modules": {
                    "include": {"topics": {"order_by": {"order": "asc"}}},
                    "order_by": {"order": "asc"}
                }
            }
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return course

    async def create_course(self, user_id: str, data: CourseCreate):
        # Basic manual creation logic needed for the service to be valid
        course_data = {
            "userId": user_id,
            "title": data.title,
            "description": data.description,
            "difficulty": data.difficulty,
            "modules": {
                "create": [] # simplified for minimal requirement
            }
        }
        return await self.db.course.create(data=course_data)
    
    async def toggle_topic_completion(self, topic_id: str, user_id: str, completed: bool):
         # Minimal stub to prevent import errors
         return None