"""
Progress domain — Data access layer.

Queries for goals, schedule blocks, study sessions, streaks,
achievements, and review items (spaced repetition).
"""

import logging
from datetime import datetime
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


class ProgressRepository:
    """Data access for progress-related entities."""

    # -----------------------------------------------------------------------
    # Goals
    # -----------------------------------------------------------------------

    async def find_goal(self, goal_id: str, user_id: str):
        return await db.goal.find_first(where={"id": goal_id, "userId": user_id})

    async def list_goals(self, user_id: str, *, where: dict[str, Any], skip: int = 0, take: int = 20, order: dict | None = None) -> tuple[list, int]:
        where["userId"] = user_id
        total = await db.goal.count(where=where)
        items = await db.goal.find_many(where=where, skip=skip, take=take, order=order or {"createdAt": "desc"})
        return items, total

    async def create_goal(self, data: dict[str, Any]):
        return await db.goal.create(data=data)

    async def update_goal(self, goal_id: str, data: dict[str, Any]):
        return await db.goal.update(where={"id": goal_id}, data=data)

    async def delete_goal(self, goal_id: str):
        return await db.goal.delete(where={"id": goal_id})

    # -----------------------------------------------------------------------
    # Study Blocks (ScheduleBlock)
    # -----------------------------------------------------------------------

    async def find_block(self, block_id: str, user_id: str):
        return await db.scheduleblock.find_first(where={"id": block_id, "userId": user_id})

    async def list_blocks(self, user_id: str, *, where: dict[str, Any], skip: int = 0, take: int = 20, order: dict | None = None) -> tuple[list, int]:
        where["userId"] = user_id
        total = await db.scheduleblock.count(where=where)
        items = await db.scheduleblock.find_many(where=where, skip=skip, take=take, order=order or {"startAt": "asc"})
        return items, total

    async def create_block(self, data: dict[str, Any]):
        return await db.scheduleblock.create(data=data)

    async def update_block(self, block_id: str, data: dict[str, Any]):
        return await db.scheduleblock.update(where={"id": block_id}, data=data)

    async def delete_block(self, block_id: str):
        return await db.scheduleblock.delete(where={"id": block_id})

    # -----------------------------------------------------------------------
    # Study Sessions
    # -----------------------------------------------------------------------

    async def find_active_session(self, user_id: str):
        return await db.studysession.find_first(
            where={"userId": user_id, "endTime": None},
            order={"startTime": "desc"},
        )

    async def create_session(self, data: dict[str, Any]):
        return await db.studysession.create(data=data)

    async def update_session(self, session_id: str, data: dict[str, Any]):
        return await db.studysession.update(where={"id": session_id}, data=data)

    async def list_sessions(self, user_id: str, *, since: datetime | None = None, course_id: str | None = None) -> list:
        where: dict[str, Any] = {"userId": user_id, "endTime": {"not": None}}
        if since:
            where["startTime"] = {"gte": since}
        if course_id:
            where["courseId"] = course_id
        return await db.studysession.find_many(where=where, order={"startTime": "desc"})

    # -----------------------------------------------------------------------
    # Streaks
    # -----------------------------------------------------------------------

    async def get_streak(self, user_id: str):
        return await db.userstreak.find_unique(where={"userId": user_id})

    async def upsert_streak(self, user_id: str, data: dict[str, Any]):
        return await db.userstreak.upsert(
            where={"userId": user_id},
            data={"create": {"userId": user_id, **data}, "update": data},
        )

    # -----------------------------------------------------------------------
    # Achievements
    # -----------------------------------------------------------------------

    async def list_achievements(self, user_id: str) -> list:
        return await db.achievement.find_many(
            where={"userId": user_id}, order={"unlockedAt": "desc"}
        )

    async def create_achievement(self, data: dict[str, Any]):
        return await db.achievement.create(data=data)

    async def get_achievement_types(self, user_id: str) -> set:
        achievements = await db.achievement.find_many(where={"userId": user_id})
        return {a.achievementType for a in achievements}

    # -----------------------------------------------------------------------
    # Review Items (Spaced Repetition)
    # -----------------------------------------------------------------------

    async def list_due_reviews(self, user_id: str, *, before: datetime | None = None) -> list:
        where: dict[str, Any] = {"userId": user_id}
        if before:
            where["nextReviewAt"] = {"lte": before}
        return await db.reviewitem.find_many(
            where=where, order={"nextReviewAt": "asc"}, include={"topic": True}
        )

    async def find_review(self, review_id: str, user_id: str):
        return await db.reviewitem.find_first(
            where={"id": review_id, "userId": user_id}, include={"topic": True}
        )

    async def update_review(self, review_id: str, data: dict[str, Any]):
        return await db.reviewitem.update(where={"id": review_id}, data=data)


# Singleton
progress_repo = ProgressRepository()
