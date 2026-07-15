"""
Knowledge domain — Data access layer.

Encapsulates all Prisma queries for Course, Module, Topic, Resource.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


class KnowledgeRepository:
    """Data access for courses, modules, topics, and resources."""

    # -----------------------------------------------------------------------
    # Courses
    # -----------------------------------------------------------------------

    async def find_course(self, course_id: str, user_id: str):
        """Find a course owned by user."""
        return await db.course.find_first(where={"id": course_id, "userId": user_id})

    async def find_course_with_modules(self, course_id: str, user_id: str):
        """Find course with full module/topic tree."""
        return await db.course.find_first(
            where={"id": course_id, "userId": user_id},
            include={"modules": {"include": {"topics": True}, "orderBy": {"order": "asc"}}},
        )

    async def list_courses(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list, int]:
        """List courses with pagination. Returns (courses, total)."""
        where["userId"] = user_id
        total = await db.course.count(where=where)
        courses = await db.course.find_many(
            where=where,
            skip=skip,
            take=take,
            order=order or {"createdAt": "desc"},
            include={"modules": {"include": {"topics": True}}},
        )
        return courses, total

    async def create_course(self, data: dict[str, Any]):
        return await db.course.create(data=data)

    async def update_course(self, course_id: str, data: dict[str, Any]):
        return await db.course.update(where={"id": course_id}, data=data)

    async def delete_course(self, course_id: str):
        return await db.course.delete(where={"id": course_id})

    async def count_courses(self, where: dict[str, Any]) -> int:
        return await db.course.count(where=where)

    # -----------------------------------------------------------------------
    # Modules
    # -----------------------------------------------------------------------

    async def find_module(self, module_id: str):
        return await db.module.find_unique(
            where={"id": module_id}, include={"course": True}
        )

    async def find_module_with_topics(self, module_id: str):
        return await db.module.find_unique(
            where={"id": module_id},
            include={"topics": {"orderBy": {"order": "asc"}}, "course": True},
        )

    async def list_modules(self, course_id: str):
        return await db.module.find_many(
            where={"courseId": course_id},
            include={"topics": True},
            order={"order": "asc"},
        )

    async def create_module(self, data: dict[str, Any]):
        return await db.module.create(data=data)

    async def update_module(self, module_id: str, data: dict[str, Any]):
        return await db.module.update(where={"id": module_id}, data=data)

    async def delete_module(self, module_id: str):
        return await db.module.delete(where={"id": module_id})

    # -----------------------------------------------------------------------
    # Topics
    # -----------------------------------------------------------------------

    async def find_topic(self, topic_id: str):
        return await db.topic.find_unique(
            where={"id": topic_id},
            include={"module": {"include": {"course": True}}},
        )

    async def create_topic(self, data: dict[str, Any]):
        return await db.topic.create(data=data, include={"notes": True})

    async def update_topic(self, topic_id: str, data: dict[str, Any]):
        return await db.topic.update(where={"id": topic_id}, data=data, include={"notes": True})

    async def delete_topic(self, topic_id: str):
        return await db.topic.delete(where={"id": topic_id})

    # -----------------------------------------------------------------------
    # Resources
    # -----------------------------------------------------------------------

    async def list_resources(
        self,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list, int]:
        total = await db.resource.count(where=where)
        resources = await db.resource.find_many(
            where=where,
            skip=skip,
            take=take,
            order=order or {"createdAt": "desc"},
        )
        return resources, total

    async def find_resource(self, resource_id: str, user_id: str):
        return await db.resource.find_first(
            where={"id": resource_id, "userId": user_id}
        )

    async def create_resource(self, data: dict[str, Any]):
        return await db.resource.create(data=data)

    async def update_resource(self, resource_id: str, data: dict[str, Any]):
        return await db.resource.update(where={"id": resource_id}, data=data)

    async def delete_resource(self, resource_id: str):
        return await db.resource.delete(where={"id": resource_id})

    # -----------------------------------------------------------------------
    # Outline Satisfaction (KPI)
    # -----------------------------------------------------------------------

    async def record_outline_satisfaction(self, data: dict[str, Any]):
        return await db.courseoutlinesatisfaction.create(data=data)

    async def has_outline_satisfaction(self, user_id: str, course_id: str) -> bool:
        count = await db.courseoutlinesatisfaction.count(
            where={"userId": user_id, "courseId": course_id}
        )
        return count > 0


# Singleton
knowledge_repo = KnowledgeRepository()
