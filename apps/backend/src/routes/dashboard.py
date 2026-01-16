"""
Dashboard routes for user dashboard overview.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from prisma import Client as PrismaClient

from ..dependencies import CurrentUser
from ..models.analytics import (
    DashboardCourseItem,
    DashboardGoalItem,
    DashboardResponse,
    DashboardScheduleItem,
    DashboardStats,
)
from ..utils.dependencies import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Get dashboard overview data for the current user.

    Returns aggregated stats, recent courses, active goals, and upcoming schedules.
    """
    try:
        user_id = current_user.id
        now = datetime.now(timezone.utc)

        # ========================================================================
        # Calculate Stats
        # ========================================================================

        # Get courses
        courses = await db.course.find_many(
            where={"userId": user_id},
            include={"modules": {"include": {"topics": True}}},
        )
        total_courses = len(courses)
        active_courses = sum(1 for c in courses if not c.archived)
        completed_courses = sum(
            1
            for course in courses
            if len([t for m in course.modules for t in m.topics]) > 0
            and all(t.completed for m in course.modules for t in m.topics)
        )

        # Get goals
        goals = await db.goal.find_many(where={"userId": user_id})
        total_goals = len(goals)
        active_goals = sum(1 for g in goals if g.status == "ACTIVE")
        completed_goals = sum(1 for g in goals if g.status == "COMPLETED")

        # Get study sessions for total study time
        sessions = await db.studysession.find_many(
            where={"userId": user_id, "endTime": {"not": None}},
        )
        total_study_minutes = sum(s.duration or 0 for s in sessions)

        # Get streak
        streak = await db.userstreak.find_unique(where={"userId": user_id})
        current_streak = streak.currentStreak if streak else 0
        longest_streak = streak.longestStreak if streak else 0

        # Get upcoming schedules (next 7 days)
        seven_days_later = now + timedelta(days=7)
        upcoming_schedules = await db.scheduleblock.find_many(
            where={
                "userId": user_id,
                "startAt": {"gte": now, "lte": seven_days_later},
            },
            order={"startAt": "asc"},
            take=10,  # Limit to 10 upcoming schedules
        )

        # ========================================================================
        # Build Stats
        # ========================================================================
        stats = DashboardStats(
            totalCourses=total_courses,
            activeCourses=active_courses,
            completedCourses=completed_courses,
            totalGoals=total_goals,
            activeGoals=active_goals,
            completedGoals=completed_goals,
            totalStudyMinutes=total_study_minutes,
            currentStreak=current_streak,
            longestStreak=longest_streak,
            upcomingSchedulesCount=len(upcoming_schedules),
        )

        # ========================================================================
        # Get Recent Courses (latest 5)
        # ========================================================================
        recent_courses_data = await db.course.find_many(
            where={"userId": user_id},
            include={"modules": {"include": {"topics": True}}},
            order={"createdAt": "desc"},
            take=5,
        )

        recent_courses = []
        for course in recent_courses_data:
            course_topics = [topic for module in course.modules for topic in module.topics]
            course_completed_topics = sum(1 for t in course_topics if t.completed)
            course_total_topics = len(course_topics)
            course_progress = (
                (course_completed_topics / course_total_topics * 100)
                if course_total_topics > 0
                else 0.0
            )

            recent_courses.append(
                DashboardCourseItem(
                    courseId=course.id,
                    title=course.title,
                    progress=course_progress,
                    totalTopics=course_total_topics,
                    completedTopics=course_completed_topics,
                    createdAt=course.createdAt.isoformat(),
                )
            )

        # ========================================================================
        # Get Active Goals (limit 5)
        # ========================================================================
        active_goals_data = await db.goal.find_many(
            where={"userId": user_id, "status": "ACTIVE"},
            order={"createdAt": "desc"},
            take=5,
        )

        active_goals_list = [
            DashboardGoalItem(
                goalId=goal.id,
                title=goal.title,
                description=goal.description,
                progress=goal.progress,
                targetDate=goal.targetDate.isoformat() if goal.targetDate else None,
                status=goal.status,
                createdAt=goal.createdAt.isoformat(),
            )
            for goal in active_goals_data
        ]

        # ========================================================================
        # Get Upcoming Schedules (next 7 days)
        # ========================================================================
        upcoming_schedules_list = [
            DashboardScheduleItem(
                scheduleId=schedule.id,
                title=schedule.title,
                description=schedule.description,
                startAt=schedule.startAt.isoformat(),
                endAt=schedule.endAt.isoformat(),
                courseId=getattr(schedule, "courseId", None),
                topicId=getattr(schedule, "topicId", None),
                goalId=getattr(schedule, "goalId", None),
            )
            for schedule in upcoming_schedules
        ]

        # ========================================================================
        # Calculate Daily Goal Progress
        # ========================================================================
        # Get today's study sessions
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_sessions = await db.studysession.find_many(
            where={
                "userId": user_id,
                "startTime": {"gte": today_start},
                "endTime": {"not": None},
            },
        )
        today_study_minutes = sum(s.duration or 0 for s in today_sessions)

        # Assume daily goal is 60 minutes (can be made configurable later)
        daily_goal_minutes = 60.0
        daily_goal_progress = (
            min((today_study_minutes / daily_goal_minutes) * 100, 100.0)
            if daily_goal_minutes > 0
            else 0.0
        )

        return DashboardResponse(
            stats=stats,
            recentCourses=recent_courses,
            activeGoals=active_goals_list,
            upcomingSchedules=upcoming_schedules_list,
            dailyGoalProgress=daily_goal_progress,
        )

    except Exception as e:
        logger.error(f"Error in get_dashboard: {str(e)}", exc_info=True)
        raise
