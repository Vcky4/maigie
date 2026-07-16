"""
Progress domain — Data access layer (SQLAlchemy).

Queries for goals, schedule blocks, study sessions, streaks,
achievements, and review items (spaced repetition).
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, update, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from .db_models import (
    Achievement,
    Goal,
    ReviewItem,
    ScheduleBehaviourLog,
    ScheduleBlock,
    StudySession,
    UserStreak,
)

logger = logging.getLogger(__name__)


class ProgressRepository:
    """Data access for progress-related entities."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Goals
    # -----------------------------------------------------------------------

    async def find_goal(self, goal_id: str, user_id: str) -> Goal | None:
        async with await self._session() as session:
            stmt = select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_goals(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list[Goal], int]:
        async with await self._session() as session:
            conditions = [Goal.user_id == user_id]
            conditions.extend(self._build_goal_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Goal).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch
            stmt = select(Goal).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Goal, self._to_goal_attr(col_name), Goal.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())
            else:
                stmt = stmt.order_by(Goal.created_at.desc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def create_goal(self, data: dict[str, Any]) -> Goal:
        async with await self._session() as session:
            goal = Goal(**self._map_goal_data(data))
            session.add(goal)
            await session.commit()
            await session.refresh(goal)
            return goal

    async def update_goal(self, goal_id: str, data: dict[str, Any]) -> Goal:
        async with await self._session() as session:
            mapped = self._map_goal_data(data)
            stmt = update(Goal).where(Goal.id == goal_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        # Refetch to return updated object
        async with await self._session() as session:
            result = await session.execute(select(Goal).where(Goal.id == goal_id))
            return result.scalar_one()

    async def delete_goal(self, goal_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Goal).where(Goal.id == goal_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Study Blocks (ScheduleBlock)
    # -----------------------------------------------------------------------

    async def find_block(self, block_id: str, user_id: str) -> ScheduleBlock | None:
        async with await self._session() as session:
            stmt = select(ScheduleBlock).where(
                ScheduleBlock.id == block_id, ScheduleBlock.user_id == user_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_blocks(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 50,
        order: dict | None = None,
    ) -> tuple[list[ScheduleBlock], int]:
        async with await self._session() as session:
            conditions = [ScheduleBlock.user_id == user_id]
            conditions.extend(self._build_block_conditions(where))

            count_stmt = select(func.count()).select_from(ScheduleBlock).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = select(ScheduleBlock).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(ScheduleBlock, self._to_block_attr(col_name), ScheduleBlock.start_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())
            else:
                stmt = stmt.order_by(ScheduleBlock.start_at.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def create_block(self, data: dict[str, Any]) -> ScheduleBlock:
        async with await self._session() as session:
            block = ScheduleBlock(**self._map_block_data(data))
            session.add(block)
            await session.commit()
            await session.refresh(block)
            return block

    async def update_block(self, block_id: str, data: dict[str, Any]) -> ScheduleBlock:
        async with await self._session() as session:
            mapped = self._map_block_data(data)
            stmt = update(ScheduleBlock).where(ScheduleBlock.id == block_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        async with await self._session() as session:
            result = await session.execute(
                select(ScheduleBlock).where(ScheduleBlock.id == block_id)
            )
            return result.scalar_one()

    async def delete_block(self, block_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(ScheduleBlock).where(ScheduleBlock.id == block_id)
            await session.execute(stmt)
            await session.commit()

    async def delete_blocks_for_goal(self, goal_id: str) -> int:
        """Delete all schedule blocks linked to a goal. Returns count deleted."""
        async with await self._session() as session:
            count_stmt = (
                select(func.count())
                .select_from(ScheduleBlock)
                .where(ScheduleBlock.goal_id == goal_id)
            )
            count = (await session.execute(count_stmt)).scalar() or 0
            stmt = delete(ScheduleBlock).where(ScheduleBlock.goal_id == goal_id)
            await session.execute(stmt)
            await session.commit()
            return count

    # -----------------------------------------------------------------------
    # Study Sessions
    # -----------------------------------------------------------------------

    async def find_active_session(self, user_id: str) -> StudySession | None:
        async with await self._session() as session:
            stmt = (
                select(StudySession)
                .where(StudySession.user_id == user_id, StudySession.end_time.is_(None))
                .order_by(StudySession.start_time.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def find_session(self, session_id: str) -> StudySession | None:
        async with await self._session() as session:
            stmt = select(StudySession).where(StudySession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_session(self, data: dict[str, Any]) -> StudySession:
        async with await self._session() as session:
            study_session = StudySession(**self._map_session_data(data))
            session.add(study_session)
            await session.commit()
            await session.refresh(study_session)
            return study_session

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_session_data(data)
            stmt = update(StudySession).where(StudySession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def list_sessions(
        self, user_id: str, *, since: datetime | None = None, course_id: str | None = None
    ) -> list[StudySession]:
        async with await self._session() as session:
            conditions = [StudySession.user_id == user_id, StudySession.end_time.isnot(None)]
            if since:
                conditions.append(StudySession.start_time >= since)
            if course_id:
                conditions.append(StudySession.course_id == course_id)

            stmt = select(StudySession).where(*conditions).order_by(StudySession.start_time.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Streaks
    # -----------------------------------------------------------------------

    async def get_streak(self, user_id: str) -> UserStreak | None:
        async with await self._session() as session:
            stmt = select(UserStreak).where(UserStreak.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def upsert_streak(self, user_id: str, data: dict[str, Any]) -> UserStreak:
        async with await self._session() as session:
            stmt = select(UserStreak).where(UserStreak.user_id == user_id)
            result = await session.execute(stmt)
            streak = result.scalar_one_or_none()

            mapped = self._map_streak_data(data)
            if streak:
                for key, value in mapped.items():
                    setattr(streak, key, value)
                await session.commit()
                await session.refresh(streak)
                return streak
            else:
                streak = UserStreak(user_id=user_id, **mapped)
                session.add(streak)
                await session.commit()
                await session.refresh(streak)
                return streak

    # -----------------------------------------------------------------------
    # Achievements
    # -----------------------------------------------------------------------

    async def list_achievements(self, user_id: str) -> list[Achievement]:
        async with await self._session() as session:
            stmt = (
                select(Achievement)
                .where(Achievement.user_id == user_id)
                .order_by(Achievement.unlocked_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_achievement(self, data: dict[str, Any]) -> Achievement:
        async with await self._session() as session:
            achievement = Achievement(**self._map_achievement_data(data))
            session.add(achievement)
            await session.commit()
            await session.refresh(achievement)
            return achievement

    async def get_achievement_types(self, user_id: str) -> set:
        async with await self._session() as session:
            stmt = select(Achievement.achievement_type).where(Achievement.user_id == user_id)
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

    # -----------------------------------------------------------------------
    # Review Items (Spaced Repetition)
    # -----------------------------------------------------------------------

    async def list_due_reviews(
        self, user_id: str, *, before: datetime | None = None
    ) -> list[ReviewItem]:
        async with await self._session() as session:
            conditions = [ReviewItem.user_id == user_id]
            if before:
                conditions.append(ReviewItem.next_review_at <= before)
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(*conditions)
                .order_by(ReviewItem.next_review_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_review(self, review_id: str, user_id: str) -> ReviewItem | None:
        async with await self._session() as session:
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic), selectinload(ReviewItem.schedule_block))
                .where(ReviewItem.id == review_id, ReviewItem.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_review_by_topic(self, user_id: str, topic_id: str) -> ReviewItem | None:
        async with await self._session() as session:
            stmt = select(ReviewItem).where(
                ReviewItem.user_id == user_id, ReviewItem.topic_id == topic_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_review_item(self, data: dict[str, Any]) -> ReviewItem:
        async with await self._session() as session:
            review = ReviewItem(**self._map_review_data(data))
            session.add(review)
            await session.commit()
            await session.refresh(review)
            return review

    async def update_review(self, review_id: str, data: dict[str, Any]) -> ReviewItem:
        async with await self._session() as session:
            mapped = self._map_review_data(data)
            stmt = update(ReviewItem).where(ReviewItem.id == review_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        async with await self._session() as session:
            result = await session.execute(
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(ReviewItem.id == review_id)
            )
            return result.scalar_one()

    async def list_all_reviews(self, user_id: str) -> list[ReviewItem]:
        """All review items for a user (for stats)."""
        async with await self._session() as session:
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(ReviewItem.user_id == user_id)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Schedule Behaviour Logs
    # -----------------------------------------------------------------------

    async def create_behaviour_log(self, data: dict[str, Any]) -> ScheduleBehaviourLog:
        async with await self._session() as session:
            log = ScheduleBehaviourLog(**self._map_behaviour_data(data))
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    def _build_goal_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "status" in where:
            conditions.append(Goal.status == where["status"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Goal.space_id.is_(None))
            else:
                conditions.append(Goal.space_id == where["spaceId"])
        if "courseId" in where:
            conditions.append(Goal.course_id == where["courseId"])
        return conditions

    def _build_block_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "courseId" in where:
            conditions.append(ScheduleBlock.course_id == where["courseId"])
        if "goalId" in where:
            conditions.append(ScheduleBlock.goal_id == where["goalId"])
        if "endAt" in where and isinstance(where["endAt"], dict):
            if "gte" in where["endAt"]:
                conditions.append(ScheduleBlock.end_at >= where["endAt"]["gte"])
        if "startAt" in where and isinstance(where["startAt"], dict):
            if "lte" in where["startAt"]:
                conditions.append(ScheduleBlock.start_at <= where["startAt"]["lte"])
        return conditions

    _GOAL_FIELD_MAP = {
        "userId": "user_id",
        "title": "title",
        "description": "description",
        "targetDate": "target_date",
        "status": "status",
        "progress": "progress",
        "courseId": "course_id",
        "topicId": "topic_id",
        "spaceId": "space_id",
    }

    _BLOCK_FIELD_MAP = {
        "userId": "user_id",
        "title": "title",
        "description": "description",
        "startAt": "start_at",
        "endAt": "end_at",
        "recurringRule": "recurring_rule",
        "googleCalendarEventId": "google_calendar_event_id",
        "googleCalendarSyncedAt": "google_calendar_synced_at",
        "courseId": "course_id",
        "topicId": "topic_id",
        "goalId": "goal_id",
        "reviewItemId": "review_item_id",
        "examPrepId": "exam_prep_id",
    }

    _SESSION_FIELD_MAP = {
        "userId": "user_id",
        "startTime": "start_time",
        "endTime": "end_time",
        "duration": "duration",
        "courseId": "course_id",
        "topicId": "topic_id",
        "spaceId": "space_id",
        "metadata": "metadata_json",
    }

    _STREAK_FIELD_MAP = {
        "currentStreak": "current_streak",
        "longestStreak": "longest_streak",
        "lastStudyDate": "last_study_date",
    }

    _REVIEW_FIELD_MAP = {
        "userId": "user_id",
        "topicId": "topic_id",
        "nextReviewAt": "next_review_at",
        "intervalDays": "interval_days",
        "repetitionCount": "repetition_count",
        "easeFactor": "ease_factor",
        "lastQuality": "last_quality",
        "lapseCount": "lapse_count",
        "lastReviewedAt": "last_reviewed_at",
    }

    _ACHIEVEMENT_FIELD_MAP = {
        "userId": "user_id",
        "achievementType": "achievement_type",
        "title": "title",
        "description": "description",
        "icon": "icon",
        "metadata": "metadata_json",
        "unlockedAt": "unlocked_at",
    }

    _BEHAVIOUR_FIELD_MAP = {
        "userId": "user_id",
        "behaviourType": "behaviour_type",
        "entityType": "entity_type",
        "entityId": "entity_id",
        "scheduledAt": "scheduled_at",
        "actualAt": "actual_at",
        "metadata": "metadata_json",
    }

    def _map_goal_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._GOAL_FIELD_MAP.get(k, k): v for k, v in data.items() if k in self._GOAL_FIELD_MAP
        }

    def _map_block_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._BLOCK_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._BLOCK_FIELD_MAP
        }

    def _map_session_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._SESSION_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._SESSION_FIELD_MAP
        }

    def _map_streak_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._STREAK_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._STREAK_FIELD_MAP
        }

    def _map_review_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._REVIEW_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._REVIEW_FIELD_MAP
        }

    def _map_achievement_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._ACHIEVEMENT_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._ACHIEVEMENT_FIELD_MAP
        }

    def _map_behaviour_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            self._BEHAVIOUR_FIELD_MAP.get(k, k): v
            for k, v in data.items()
            if k in self._BEHAVIOUR_FIELD_MAP
        }

    def _to_goal_attr(self, col_name: str) -> str:
        return self._GOAL_FIELD_MAP.get(col_name, col_name)

    def _to_block_attr(self, col_name: str) -> str:
        return self._BLOCK_FIELD_MAP.get(col_name, col_name)


# Singleton
progress_repo = ProgressRepository()
