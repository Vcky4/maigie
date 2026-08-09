"""
Knowledge domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Course, Module, Topic, Resource.
"""

import logging
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from .db_models import Course, CourseOutlineSatisfaction, Module, Resource, Topic

logger = logging.getLogger(__name__)


class KnowledgeRepository:
    """Data access for courses, modules, topics, and resources."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    async def get_course_dashboard_stats(self, user_id: str) -> tuple[int, int]:
        """Return unarchived course and completed-topic counts for one user."""
        async with await self._session() as session:
            course_count_stmt = (
                select(func.count())
                .select_from(Course)
                .where(Course.user_id == user_id, Course.archived.is_(False))
            )
            completed_topics_stmt = (
                select(func.count())
                .select_from(Topic)
                .join(Module, Topic.module_id == Module.id)
                .join(Course, Module.course_id == Course.id)
                .where(
                    Course.user_id == user_id,
                    Course.archived.is_(False),
                    Topic.completed.is_(True),
                )
            )
            active_courses = (await session.execute(course_count_stmt)).scalar_one() or 0
            completed_topics = (await session.execute(completed_topics_stmt)).scalar_one() or 0
            return active_courses, completed_topics

    # -----------------------------------------------------------------------
    # Courses
    # -----------------------------------------------------------------------

    async def find_course(self, course_id: str, user_id: str) -> Course | None:
        async with await self._session() as session:
            stmt = select(Course).where(Course.id == course_id, Course.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_course_with_modules(self, course_id: str, user_id: str) -> Course | None:
        async with await self._session() as session:
            stmt = (
                select(Course)
                .options(selectinload(Course.modules).selectinload(Module.topics))
                .where(Course.id == course_id, Course.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_courses(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list[Course], int]:
        async with await self._session() as session:
            conditions = [Course.user_id == user_id]
            conditions.extend(self._build_course_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Course).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch
            stmt = (
                select(Course)
                .options(selectinload(Course.modules).selectinload(Module.topics))
                .where(*conditions)
                .offset(skip)
                .limit(take)
            )
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Course, self._to_attr(col_name), Course.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def create_course(self, data: dict[str, Any]) -> Course:
        async with await self._session() as session:
            course = Course(**self._map_course_data(data))
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course

    async def update_course(self, course_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = (
                update(Course).where(Course.id == course_id).values(**self._map_course_data(data))
            )
            await session.execute(stmt)
            await session.commit()

    async def delete_course(self, course_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Course).where(Course.id == course_id)
            await session.execute(stmt)
            await session.commit()

    async def count_courses(self, where: dict[str, Any]) -> int:
        async with await self._session() as session:
            conditions = self._build_course_conditions(where)
            stmt = (
                select(func.count()).select_from(Course).where(*conditions)
                if conditions
                else select(func.count()).select_from(Course)
            )
            return (await session.execute(stmt)).scalar() or 0

    # -----------------------------------------------------------------------
    # Modules
    # -----------------------------------------------------------------------

    async def find_module(self, module_id: str) -> Module | None:
        async with await self._session() as session:
            stmt = select(Module).options(selectinload(Module.course)).where(Module.id == module_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_module_with_topics(self, module_id: str) -> Module | None:
        async with await self._session() as session:
            stmt = (
                select(Module)
                .options(selectinload(Module.topics), selectinload(Module.course))
                .where(Module.id == module_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_modules(self, course_id: str) -> list[Module]:
        async with await self._session() as session:
            stmt = (
                select(Module)
                .options(selectinload(Module.topics))
                .where(Module.course_id == course_id)
                .order_by(Module.order.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_module(self, data: dict[str, Any]) -> Module:
        async with await self._session() as session:
            module = Module(
                course_id=data.get("courseId"),
                title=data["title"],
                order=data.get("order", 0),
                description=data.get("description"),
            )
            session.add(module)
            await session.commit()
            await session.refresh(module)
            return module

    async def update_module(self, module_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = {}
            if "title" in data:
                mapped["title"] = data["title"]
            if "order" in data:
                mapped["order"] = data["order"]
            if "description" in data:
                mapped["description"] = data["description"]
            if mapped:
                stmt = update(Module).where(Module.id == module_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

    async def delete_module(self, module_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Module).where(Module.id == module_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Topics
    # -----------------------------------------------------------------------

    async def find_topic(self, topic_id: str) -> Topic | None:
        async with await self._session() as session:
            stmt = (
                select(Topic)
                .options(selectinload(Topic.module).selectinload(Module.course))
                .where(Topic.id == topic_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_topic(self, data: dict[str, Any]) -> Topic:
        async with await self._session() as session:
            topic = Topic(
                module_id=data.get("moduleId"),
                title=data["title"],
                order=data.get("order", 0),
                content=data.get("content"),
                estimated_hours=data.get("estimatedHours"),
            )
            session.add(topic)
            await session.commit()
            await session.refresh(topic)
            return topic

    async def update_topic(self, topic_id: str, data: dict[str, Any]) -> Topic:
        async with await self._session() as session:
            mapped = {}
            if "title" in data:
                mapped["title"] = data["title"]
            if "order" in data:
                mapped["order"] = data["order"]
            if "content" in data:
                mapped["content"] = data["content"]
            if "estimatedHours" in data:
                mapped["estimated_hours"] = data["estimatedHours"]
            if "completed" in data:
                mapped["completed"] = data["completed"]
            if mapped:
                stmt = update(Topic).where(Topic.id == topic_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()
            # Refetch
            return await self.find_topic(topic_id)

    async def delete_topic(self, topic_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Topic).where(Topic.id == topic_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Resources
    # -----------------------------------------------------------------------

    async def list_resources(
        self, *, where: dict[str, Any], skip: int = 0, take: int = 20, order: dict | None = None
    ) -> tuple[list[Resource], int]:
        async with await self._session() as session:
            conditions = self._build_resource_conditions(where)

            count_stmt = (
                select(func.count()).select_from(Resource).where(*conditions)
                if conditions
                else select(func.count()).select_from(Resource)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = select(Resource).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Resource, self._to_attr(col_name), Resource.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def find_resource(self, resource_id: str, user_id: str) -> Resource | None:
        async with await self._session() as session:
            stmt = select(Resource).where(Resource.id == resource_id, Resource.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_resource(self, data: dict[str, Any]) -> Resource:
        async with await self._session() as session:
            resource = Resource(
                user_id=data["userId"],
                title=data["title"],
                url=data["url"],
                type=data.get("type", "OTHER"),
                description=data.get("description"),
                metadata_json=data.get("metadata"),
                is_recommended=data.get("isRecommended", False),
                recommendation_score=data.get("recommendationScore"),
                recommendation_source=data.get("recommendationSource"),
                course_id=data.get("courseId"),
                topic_id=data.get("topicId"),
                space_id=data.get("spaceId"),
            )
            session.add(resource)
            await session.commit()
            await session.refresh(resource)
            return resource

    async def update_resource(self, resource_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = update(Resource).where(Resource.id == resource_id).values(**data)
            await session.execute(stmt)
            await session.commit()

    async def delete_resource(self, resource_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Resource).where(Resource.id == resource_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Outline Satisfaction
    # -----------------------------------------------------------------------

    async def record_outline_satisfaction(self, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            record = CourseOutlineSatisfaction(
                user_id=data["userId"],
                course_id=data["courseId"],
                kind=data["kind"],
                feedback=data.get("feedback"),
            )
            session.add(record)
            await session.commit()

    async def has_outline_satisfaction(self, user_id: str, course_id: str) -> bool:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(CourseOutlineSatisfaction)
                .where(
                    CourseOutlineSatisfaction.user_id == user_id,
                    CourseOutlineSatisfaction.course_id == course_id,
                )
            )
            count = (await session.execute(stmt)).scalar() or 0
            return count > 0

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_course_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "userId" in where:
            conditions.append(Course.user_id == where["userId"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Course.space_id.is_(None))
            else:
                conditions.append(Course.space_id == where["spaceId"])
        if "archived" in where:
            conditions.append(Course.archived == where["archived"])
        if "difficulty" in where:
            conditions.append(Course.difficulty == where["difficulty"])
        if "isAIGenerated" in where:
            conditions.append(Course.is_ai_generated == where["isAIGenerated"])
        if "createdAt" in where and isinstance(where["createdAt"], dict):
            gte = where["createdAt"].get("gte")
            if gte:
                conditions.append(Course.created_at >= gte)
        return conditions

    def _build_resource_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "userId" in where:
            conditions.append(Resource.user_id == where["userId"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Resource.space_id.is_(None))
            else:
                conditions.append(Resource.space_id == where["spaceId"])
        if "topicId" in where:
            conditions.append(Resource.topic_id == where["topicId"])
        if "courseId" in where:
            conditions.append(Resource.course_id == where["courseId"])
        if "type" in where:
            conditions.append(Resource.type == where["type"])
        return conditions

    def _map_course_data(self, data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "isAIGenerated": "is_ai_generated",
            "targetDate": "target_date",
            "spaceId": "space_id",
        }
        result = {}
        for key, value in data.items():
            attr = field_map.get(key, key)
            result[attr] = value
        return result

    def _to_attr(self, col_name: str) -> str:
        mapping = {
            "createdAt": "created_at",
            "updatedAt": "updated_at",
        }
        return mapping.get(col_name, col_name)


# Singleton
knowledge_repo = KnowledgeRepository()
