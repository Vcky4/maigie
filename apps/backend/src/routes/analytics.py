"""
Analytics routes for comprehensive user and admin analytics.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Optional, Union

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from prisma import Client as PrismaClient

from ..dependencies import AdminUser, CurrentUser
from ..models.analytics import (
    AchievementBadge,
    AIGeneratedContentStats,
    AIMessageStats,
    AIUsageAnalytics,
    AIFeatureUsage,
    AIMetrics,
    CourseAnalyticsItem,
    EnhancedAdminAnalyticsResponse,
    EnhancedUserAnalyticsResponse,
    GoalComparison,
    InsightsAndReports,
    LearningPaceTrend,
    MonthlyReport,
    ProgressAnalytics,
    PlatformStatistics,
    ProductiveTimeSlot,
    Recommendation,
    RetentionMetrics,
    ScheduleAdherence,
    SessionDurationStats,
    StudyAnalytics,
    StudyStreak,
    StudyTimeByCourse,
    StudyTimeBySubject,
    StudyTimePeriod,
    SubscriptionFunnelMetrics,
    UserAnalyticsItem,
    UserBehaviorMetrics,
    VoiceInteractionStats,
    WeeklyReport,
)
from ..utils.dependencies import get_db_client
from ..utils.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


# ============================================================================
# Study Session Tracking
# ============================================================================


class StartSessionRequest(BaseModel):
    courseId: Optional[str] = None
    topicId: Optional[str] = None


@router.post("/sessions/start")
async def start_study_session(
    current_user: CurrentUser,
    request: Optional[StartSessionRequest] = Body(default=None),
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """Start a new study session."""
    try:
        # Handle optional request body (all fields are optional in the model)
        if request is None:
            request = StartSessionRequest()

        course_id = request.courseId if request.courseId else None
        topic_id = request.topicId if request.topicId else None

        logger.info(
            f"Starting study session for user {current_user.id}, course: {course_id}, topic: {topic_id}"
        )

        # Check if there's an active session
        active_session = await db.studysession.find_first(
            where={
                "userId": current_user.id,
                "endTime": None,
            },
            order={"startTime": "desc"},
        )

        if active_session:
            logger.info(f"Active session already exists: {active_session.id}")
            # Return existing session
            return {
                "sessionId": active_session.id,
                "startTime": active_session.startTime.isoformat(),
                "message": "Active session already exists",
            }

        # Create new session
        session = await db.studysession.create(
            data={
                "userId": current_user.id,
                "startTime": datetime.utcnow(),
                "courseId": course_id,
                "topicId": topic_id,
            }
        )

        logger.info(f"Created new study session: {session.id}")

        return {
            "sessionId": session.id,
            "startTime": session.startTime.isoformat(),
        }
    except Exception as e:
        logger.error(
            f"Error starting study session for user {current_user.id}: {str(e)}",
            exc_info=True,
        )
        raise


@router.post("/sessions/{session_id}/stop")
async def stop_study_session(
    session_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """Stop an active study session."""
    try:
        logger.info(f"Stopping study session {session_id} for user {current_user.id}")

        session = await db.studysession.find_unique(where={"id": session_id})

        if not session:
            logger.warning(f"Study session {session_id} not found")
            raise ResourceNotFoundError("StudySession", session_id)

        if session.userId != current_user.id:
            logger.warning(
                f"User {current_user.id} attempted to stop session {session_id} owned by {session.userId}"
            )
            raise ResourceNotFoundError("StudySession", session_id)

        if session.endTime:
            logger.info(f"Session {session_id} already ended")
            return {
                "sessionId": session.id,
                "duration": session.duration,
                "message": "Session already ended",
            }

        # Calculate duration
        end_time = datetime.utcnow()
        duration_minutes = (end_time - session.startTime).total_seconds() / 60

        # Update session
        updated_session = await db.studysession.update(
            where={"id": session_id},
            data={
                "endTime": end_time,
                "duration": duration_minutes,
            },
        )

        logger.info(f"Session {session_id} stopped. Duration: {duration_minutes:.2f} minutes")

        # Update streak
        await _update_streak(db, current_user.id, end_time.date())

        # Check and unlock achievements
        await _check_and_unlock_achievements(db, current_user.id, updated_session)

        return {
            "sessionId": updated_session.id,
            "duration": updated_session.duration,
            "endTime": updated_session.endTime.isoformat(),
        }
    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error stopping study session {session_id}: {str(e)}", exc_info=True)
        raise


# ============================================================================
# Enhanced User Analytics
# ============================================================================


@router.get("/user/enhanced", response_model=EnhancedUserAnalyticsResponse)
async def get_enhanced_user_analytics(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """Get comprehensive enhanced analytics for the current user."""
    try:
        user_id = current_user.id

        # Get basic analytics (calculate inline to avoid circular import)
        from ..models.analytics import CourseProgressItem, UserProgressSummary

        # Fetch all user courses
        courses_data = await db.course.find_many(
            where={"userId": user_id},
            include={"modules": {"include": {"topics": True}}},
            order={"createdAt": "desc"},
        )

        # Calculate overall statistics
        total_courses = len(courses_data)
        active_courses = sum(1 for c in courses_data if not c.archived)
        archived_courses = sum(1 for c in courses_data if c.archived)

        total_modules = sum(len(c.modules) for c in courses_data)
        total_topics = sum(len(module.topics) for c in courses_data for module in c.modules)
        completed_topics = sum(
            1
            for c in courses_data
            for module in c.modules
            for topic in module.topics
            if topic.completed
        )

        # Calculate completed modules
        completed_modules = 0
        for course in courses_data:
            for module in course.modules:
                if len(module.topics) > 0 and all(topic.completed for topic in module.topics):
                    completed_modules += 1

        # Calculate completed courses
        completed_courses = 0
        for course in courses_data:
            course_topics = [topic for module in course.modules for topic in module.topics]
            if len(course_topics) > 0 and all(topic.completed for topic in course_topics):
                completed_courses += 1

        # Calculate estimated hours
        total_estimated_hours = 0.0
        completed_estimated_hours = 0.0
        for course in courses_data:
            for module in course.modules:
                for topic in module.topics:
                    if topic.estimatedHours:
                        total_estimated_hours += topic.estimatedHours
                        if topic.completed:
                            completed_estimated_hours += topic.estimatedHours

        # Calculate overall progress
        overall_progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0

        # Calculate average course progress
        course_progresses = []
        for course in courses_data:
            course_topics = [topic for module in course.modules for topic in module.topics]
            if len(course_topics) > 0:
                completed = sum(1 for t in course_topics if t.completed)
                progress = (completed / len(course_topics)) * 100
                course_progresses.append(progress)

        average_course_progress = (
            sum(course_progresses) / len(course_progresses) if course_progresses else 0.0
        )

        # Build summary
        summary = UserProgressSummary(
            userId=user_id,
            totalCourses=total_courses,
            activeCourses=active_courses,
            completedCourses=completed_courses,
            archivedCourses=archived_courses,
            totalModules=total_modules,
            completedModules=completed_modules,
            totalTopics=total_topics,
            completedTopics=completed_topics,
            overallProgress=overall_progress,
            totalEstimatedHours=total_estimated_hours,
            completedEstimatedHours=completed_estimated_hours,
            averageCourseProgress=average_course_progress,
        )

        # Build course progress items
        courses = []
        for course in courses_data:
            course_topics = [topic for module in course.modules for topic in module.topics]
            course_completed_topics = sum(1 for t in course_topics if t.completed)
            course_total_topics = len(course_topics)
            course_progress = (
                (course_completed_topics / course_total_topics * 100)
                if course_total_topics > 0
                else 0.0
            )

            courses.append(
                CourseProgressItem(
                    courseId=course.id,
                    title=course.title,
                    progress=course_progress,
                    totalTopics=course_total_topics,
                    completedTopics=course_completed_topics,
                    totalModules=len(course.modules),
                    completedModules=sum(
                        1
                        for module in course.modules
                        if len(module.topics) > 0
                        and all(topic.completed for topic in module.topics)
                    ),
                    isArchived=course.archived,
                    createdAt=course.createdAt.isoformat(),
                )
            )

        # Get study analytics
        study_analytics = await _get_study_analytics(db, user_id)

        # Get progress analytics
        progress_analytics = await _get_progress_analytics(db, user_id)

        # Get AI usage analytics
        ai_usage_analytics = await _get_ai_usage_analytics(db, user_id)

        # Get insights and reports
        insights_and_reports = await _get_insights_and_reports(db, user_id)

        return EnhancedUserAnalyticsResponse(
            summary=summary,
            courses=courses,
            studyAnalytics=study_analytics,
            progressAnalytics=progress_analytics,
            aiUsageAnalytics=ai_usage_analytics,
            insightsAndReports=insights_and_reports,
        )
    except Exception as e:
        logger.error(f"Error in get_enhanced_user_analytics: {str(e)}", exc_info=True)
        raise


# ============================================================================
# Helper Functions
# ============================================================================


async def _update_streak(db: PrismaClient, user_id: str, study_date: datetime.date):
    """Update user's study streak."""
    streak = await db.userstreak.find_unique(where={"userId": user_id})

    if not streak:
        streak = await db.userstreak.create(
            data={
                "userId": user_id,
                "currentStreak": 0,
                "longestStreak": 0,
            }
        )

    # Check if this is a new day
    if streak.lastStudyDate:
        last_date = (
            streak.lastStudyDate.date()
            if isinstance(streak.lastStudyDate, datetime)
            else streak.lastStudyDate
        )
        days_diff = (study_date - last_date).days

        if days_diff == 0:
            # Same day, no update needed
            return
        elif days_diff == 1:
            # Consecutive day
            new_streak = streak.currentStreak + 1
        else:
            # Streak broken
            new_streak = 1
    else:
        # First study session
        new_streak = 1

    longest_streak = max(streak.longestStreak, new_streak)

    await db.userstreak.update(
        where={"userId": user_id},
        data={
            "currentStreak": new_streak,
            "longestStreak": longest_streak,
            "lastStudyDate": study_date,
        },
    )


async def _get_study_analytics(db: PrismaClient, user_id: str) -> StudyAnalytics:
    """Get comprehensive study analytics."""
    try:
        now = datetime.utcnow()

        # Get all completed sessions
        sessions = await db.studysession.find_many(
            where={
                "userId": user_id,
                "endTime": {"not": None},
            },
            include={"course": True, "topic": True},
            order={"startTime": "desc"},
        )

        # Daily study time (last 30 days)
        daily_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
        for session in sessions:
            if session.endTime:
                date_str = session.startTime.date().isoformat()
                daily_data[date_str]["minutes"] += session.duration or 0
                daily_data[date_str]["sessions"] += 1

        daily = [
            StudyTimePeriod(date=date, minutes=data["minutes"], sessions=data["sessions"])
            for date, data in sorted(daily_data.items())[-30:]
        ]

        # Weekly study time (last 12 weeks)
        weekly_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
        for session in sessions:
            if session.endTime:
                week_start = session.startTime - timedelta(days=session.startTime.weekday())
                week_str = week_start.date().isoformat()
                weekly_data[week_str]["minutes"] += session.duration or 0
                weekly_data[week_str]["sessions"] += 1

        weekly = [
            StudyTimePeriod(date=date, minutes=data["minutes"], sessions=data["sessions"])
            for date, data in sorted(weekly_data.items())[-12:]
        ]

        # Monthly study time (last 12 months)
        monthly_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
        for session in sessions:
            if session.endTime:
                month_str = session.startTime.strftime("%Y-%m")
                monthly_data[month_str]["minutes"] += session.duration or 0
                monthly_data[month_str]["sessions"] += 1

        monthly = [
            StudyTimePeriod(date=date, minutes=data["minutes"], sessions=data["sessions"])
            for date, data in sorted(monthly_data.items())[-12:]
        ]

        # By course
        by_course_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0, "title": "Unknown"})
        for session in sessions:
            if session.endTime and session.courseId:
                course_title = session.course.title if session.course else "Unknown"
                by_course_data[session.courseId]["minutes"] += session.duration or 0
                by_course_data[session.courseId]["sessions"] += 1
                by_course_data[session.courseId]["title"] = course_title

        by_course = [
            StudyTimeByCourse(
                courseId=course_id,
                courseTitle=data["title"],
                totalMinutes=data["minutes"],
                sessionCount=data["sessions"],
                averageSessionDuration=(
                    data["minutes"] / data["sessions"] if data["sessions"] > 0 else 0
                ),
            )
            for course_id, data in by_course_data.items()
        ]

        # By subject (using course difficulty as subject for now)
        by_subject_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
        for session in sessions:
            if session.endTime and session.course:
                subject = str(session.course.difficulty)
                by_subject_data[subject]["minutes"] += session.duration or 0
                by_subject_data[subject]["sessions"] += 1

        by_subject = [
            StudyTimeBySubject(
                subject=subject,
                totalMinutes=data["minutes"],
                sessionCount=data["sessions"],
            )
            for subject, data in by_subject_data.items()
        ]

        # Streak
        streak_record = await db.userstreak.find_unique(where={"userId": user_id})
        streak = StudyStreak(
            currentStreak=streak_record.currentStreak if streak_record else 0,
            longestStreak=streak_record.longestStreak if streak_record else 0,
            lastStudyDate=(
                streak_record.lastStudyDate.isoformat()
                if streak_record and streak_record.lastStudyDate
                else None
            ),
        )

        # Productive times (by hour)
        hourly_data = defaultdict(lambda: {"minutes": 0.0, "sessions": 0})
        for session in sessions:
            if session.endTime:
                hour = session.startTime.hour
                hourly_data[hour]["minutes"] += session.duration or 0
                hourly_data[hour]["sessions"] += 1

        productive_times = [
            ProductiveTimeSlot(
                hour=hour, totalMinutes=data["minutes"], sessionCount=data["sessions"]
            )
            for hour, data in sorted(hourly_data.items())
        ]

        # Session duration stats
        durations = [s.duration for s in sessions if s.duration]
        if durations:
            durations_sorted = sorted(durations)
            session_stats = SessionDurationStats(
                average=sum(durations) / len(durations),
                median=durations_sorted[len(durations_sorted) // 2],
                min=min(durations),
                max=max(durations),
                totalSessions=len(durations),
            )
        else:
            session_stats = SessionDurationStats(
                average=0,
                median=0,
                min=0,
                max=0,
                totalSessions=0,
            )

        return StudyAnalytics(
            daily=daily,
            weekly=weekly,
            monthly=monthly,
            byCourse=by_course,
            bySubject=by_subject,
            streak=streak,
            productiveTimes=productive_times,
            sessionStats=session_stats,
        )
    except Exception as e:
        logger.error(f"Error in _get_study_analytics for user {user_id}: {str(e)}", exc_info=True)
        # Return empty analytics on error
        return StudyAnalytics(
            daily=[],
            weekly=[],
            monthly=[],
            byCourse=[],
            bySubject=[],
            streak=StudyStreak(currentStreak=0, longestStreak=0, lastStudyDate=None),
            productiveTimes=[],
            sessionStats=SessionDurationStats(average=0, median=0, min=0, max=0, totalSessions=0),
        )


async def _get_progress_analytics(db: PrismaClient, user_id: str) -> ProgressAnalytics:
    """Get progress analytics."""
    try:
        # Get courses and calculate completion rates over time
        courses = await db.course.find_many(
            where={"userId": user_id},
            include={"modules": {"include": {"topics": True}}},
        )

        # Completion rates over time (simplified - can be enhanced)
        completion_rates = []

        # Goal achievement
        goals = await db.goal.find_many(where={"userId": user_id})
        completed_goals = [g for g in goals if g.status == "COMPLETED"]
        active_goals = [g for g in goals if g.status == "ACTIVE"]

        goal_achievement_rate = (
            len(completed_goals) / len(goals) * 100 if goals else 0,
            sum(g.progress for g in goals) / len(goals) if goals else 0,
        )

        # Task completion (using goals as tasks for now)
        task_trends = []

        # Schedule adherence
        schedules = await db.scheduleblock.find_many(where={"userId": user_id})
        # Simplified - would need to compare scheduled vs actual study times
        schedule_adherence = ScheduleAdherence(
            totalScheduledBlocks=len(schedules),
            completedBlocks=0,  # Would need to track completion
            adherenceRate=0.0,
            averageDeviationMinutes=0.0,
        )

        # Learning pace trends
        pace_trends = []

        from ..models.analytics import CompletionRate, GoalAchievementRate, TaskCompletionTrend

        return ProgressAnalytics(
            courseCompletionRates=completion_rates,
            goalAchievementRate=GoalAchievementRate(
                totalGoals=len(goals),
                completedGoals=len(completed_goals),
                activeGoals=len(active_goals),
                achievementRate=goal_achievement_rate[0],
                averageProgress=goal_achievement_rate[1],
            ),
            taskCompletionTrends=task_trends,
            scheduleAdherence=schedule_adherence,
            learningPaceTrends=pace_trends,
        )
    except Exception as e:
        logger.error(
            f"Error in _get_progress_analytics for user {user_id}: {str(e)}", exc_info=True
        )

        return ProgressAnalytics(
            courseCompletionRates=[],
            goalAchievementRate=GoalAchievementRate(
                totalGoals=0, completedGoals=0, activeGoals=0, achievementRate=0, averageProgress=0
            ),
            taskCompletionTrends=[],
            scheduleAdherence=ScheduleAdherence(
                totalScheduledBlocks=0,
                completedBlocks=0,
                adherenceRate=0.0,
                averageDeviationMinutes=0.0,
            ),
            learningPaceTrends=[],
        )


async def _get_ai_usage_analytics(db: PrismaClient, user_id: str) -> AIUsageAnalytics:
    """Get AI usage analytics."""
    try:
        # Get messages
        messages = await db.chatmessage.find_many(
            where={"userId": user_id},
            order={"createdAt": "desc"},
        )

        # Message stats
        total_messages = len([m for m in messages if m.role == "USER"])
        message_stats = AIMessageStats(
            totalMessages=total_messages,
            messagesByPeriod=[],  # Can be enhanced
            averageMessagesPerDay=total_messages / 30 if total_messages > 0 else 0,
        )

        # Voice interactions
        voice_messages = [m for m in messages if m.audioUrl]
        total_voice_duration = (
            sum(m.duration or 0 for m in voice_messages) / 60
        )  # Convert to minutes

        voice_stats = VoiceInteractionStats(
            totalInteractions=len(voice_messages),
            totalDuration=total_voice_duration,
            averageDuration=total_voice_duration / len(voice_messages) if voice_messages else 0,
            interactionsByPeriod=[],
        )

        # Feature usage (simplified)
        feature_usage = []

        # Generated content
        courses = await db.course.find_many(where={"userId": user_id, "isAIGenerated": True})
        notes = await db.note.find_many(where={"userId": user_id})
        total_tokens = sum(m.tokenCount or 0 for m in messages)

        generated_content = AIGeneratedContentStats(
            coursesGenerated=len(courses),
            notesGenerated=len(notes),
            summariesGenerated=len([n for n in notes if n.summary]),
            totalTokensUsed=total_tokens,
        )

        return AIUsageAnalytics(
            messageStats=message_stats,
            voiceInteractionStats=voice_stats,
            featureUsage=feature_usage,
            generatedContentStats=generated_content,
        )
    except Exception as e:
        logger.error(
            f"Error in _get_ai_usage_analytics for user {user_id}: {str(e)}", exc_info=True
        )
        return AIUsageAnalytics(
            messageStats=AIMessageStats(
                totalMessages=0, messagesByPeriod=[], averageMessagesPerDay=0
            ),
            voiceInteractionStats=VoiceInteractionStats(
                totalInteractions=0,
                totalDuration=0,
                averageDuration=0,
                interactionsByPeriod=[],
            ),
            featureUsage=[],
            generatedContentStats=AIGeneratedContentStats(
                coursesGenerated=0,
                notesGenerated=0,
                summariesGenerated=0,
                totalTokensUsed=0,
            ),
        )


async def _get_insights_and_reports(db: PrismaClient, user_id: str) -> InsightsAndReports:
    """Get insights and reports."""
    try:
        from ..services.llm_service import GeminiService

        # Generate weekly and monthly reports
        weekly_report = await _generate_weekly_report(db, user_id)
        monthly_report = await _generate_monthly_report(db, user_id)

        # Get achievements
        user_achievements = await db.achievement.find_many(
            where={"userId": user_id},
            order={"unlockedAt": "desc"},
        )

        achievements = [
            AchievementBadge(
                id=a.id,
                type=str(a.achievementType),
                title=a.title,
                description=a.description or "",
                icon=a.icon,
                unlockedAt=a.unlockedAt.isoformat(),
                metadata=a.metadata,
            )
            for a in user_achievements
        ]

        # Generate personalized recommendations
        llm_service = GeminiService()
        goals = await db.goal.find_many(where={"userId": user_id, "status": "ACTIVE"})
        courses = await db.course.find_many(where={"userId": user_id, "archived": False})

        # Count recent achievements safely
        recent_achievements_count = 0
        try:
            for a in user_achievements:
                try:
                    unlocked_dt = (
                        datetime.fromisoformat(a.unlockedAt.replace("Z", "+00:00"))
                        if isinstance(a.unlockedAt, str)
                        else a.unlockedAt
                    )
                    if (datetime.utcnow() - unlocked_dt).days <= 7:
                        recent_achievements_count += 1
                except Exception:
                    continue
        except Exception:
            recent_achievements_count = 0

        recommendations_prompt = f"""Based on the user's learning profile, generate 3-5 personalized recommendations:

Active Goals: {len(goals)}
Active Courses: {len(courses)}
Recent Achievements: {recent_achievements_count}

Generate recommendations that are:
1. Specific and actionable
2. Aligned with user's goals
3. Prioritized (high/medium/low)
4. Focused on improvement

Format as JSON array:
[
  {{
    "type": "course|goal|schedule|study_time|topic",
    "title": "Recommendation Title",
    "description": "Why this recommendation",
    "priority": "high|medium|low"
  }}
]"""

        try:
            recs_response = await llm_service.get_chat_response([], recommendations_prompt)
            import json
            import re

            json_match = re.search(r"\[.*?\]", recs_response, re.DOTALL)
            if json_match:
                recommendations_data = json.loads(json_match.group(0))
                recommendations = [
                    Recommendation(
                        type=rec.get("type", "study_time"),
                        title=rec.get("title", "Keep Learning"),
                        description=rec.get("description", ""),
                        priority=rec.get("priority", "medium"),
                    )
                    for rec in recommendations_data
                ]
            else:
                recommendations = []
        except Exception:
            recommendations = []

        # Get goal comparisons
        goal_comparisons = []
        for goal in goals:
            goal_comparisons.append(
                GoalComparison(
                    goalId=goal.id,
                    goalTitle=goal.title,
                    targetValue=100.0,
                    currentValue=goal.progress,
                    progress=goal.progress,
                    status=(
                        "completed"
                        if goal.progress >= 100
                        else ("on_track" if goal.progress >= 50 else "behind")
                    ),
                )
            )

        return InsightsAndReports(
            weeklyReport=weekly_report,
            monthlyReport=monthly_report,
            recommendations=recommendations,
            achievements=achievements,
            goalComparisons=goal_comparisons,
        )
    except Exception as e:
        logger.error(
            f"Error in _get_insights_and_reports for user {user_id}: {str(e)}", exc_info=True
        )
        from ..models.analytics import WeeklyReport, MonthlyReport

        now = datetime.utcnow()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=6)
        month_start = datetime(now.year, now.month, 1)
        return InsightsAndReports(
            weeklyReport=WeeklyReport(
                weekStart=week_start.date().isoformat(),
                weekEnd=week_end.date().isoformat(),
                totalStudyTime=0,
                sessionsCompleted=0,
                topicsCompleted=0,
                goalsAchieved=0,
                streakMaintained=False,
                topCourses=[],
                insights=[],
            ),
            monthlyReport=MonthlyReport(
                month=now.strftime("%B"),
                year=now.year,
                totalStudyTime=0,
                sessionsCompleted=0,
                coursesCompleted=0,
                topicsCompleted=0,
                goalsAchieved=0,
                averageStreak=0.0,
                achievementsUnlocked=0,
                insights=[],
            ),
            recommendations=[],
            achievements=[],
            goalComparisons=[],
        )


# ============================================================================
# Enhanced Admin Analytics
# ============================================================================


@router.get("/admin/enhanced", response_model=EnhancedAdminAnalyticsResponse)
async def get_enhanced_admin_analytics(
    admin_user: AdminUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """Get comprehensive enhanced admin analytics."""
    # Get basic platform stats (calculate inline to avoid circular import)

    # Get all users
    all_users = await db.user.find_many(where={"role": "USER"})
    active_users = [u for u in all_users if u.isActive]

    # Get all courses with modules and topics
    all_courses = await db.course.find_many(
        include={"modules": {"include": {"topics": True}}, "user": True},
    )
    active_courses = [c for c in all_courses if not c.archived]

    # Calculate platform statistics
    total_modules = sum(len(c.modules) for c in all_courses)
    total_topics = sum(len(module.topics) for c in all_courses for module in c.modules)
    completed_topics = sum(
        1 for c in all_courses for module in c.modules for topic in module.topics if topic.completed
    )

    # Calculate estimated hours
    total_estimated_hours = 0.0
    completed_estimated_hours = 0.0
    for course in all_courses:
        for module in course.modules:
            for topic in module.topics:
                if topic.estimatedHours:
                    total_estimated_hours += topic.estimatedHours
                    if topic.completed:
                        completed_estimated_hours += topic.estimatedHours

    # Calculate average course progress
    course_progresses = []
    for course in all_courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0:
            completed = sum(1 for t in course_topics if t.completed)
            progress = (completed / len(course_topics)) * 100
            course_progresses.append(progress)

    average_course_progress = (
        sum(course_progresses) / len(course_progresses) if course_progresses else 0.0
    )

    # Calculate average user progress
    user_progresses = []
    for user in all_users:
        user_courses = await db.course.find_many(
            where={"userId": user.id},
            include={"modules": {"include": {"topics": True}}},
        )
        user_topics = [
            topic for course in user_courses for module in course.modules for topic in module.topics
        ]
        user_total_topics = len(user_topics)
        user_completed_topics = sum(1 for t in user_topics if t.completed)
        user_progress = (
            (user_completed_topics / user_total_topics * 100) if user_total_topics > 0 else 0.0
        )
        user_progresses.append(user_progress)

    average_user_progress = sum(user_progresses) / len(user_progresses) if user_progresses else 0.0

    # Users by tier
    users_by_tier = defaultdict(int)
    for user in all_users:
        users_by_tier[str(user.tier)] += 1

    # Courses by difficulty
    courses_by_difficulty = defaultdict(int)
    for course in all_courses:
        courses_by_difficulty[str(course.difficulty)] += 1

    # AI generated vs manual
    ai_generated = sum(1 for c in all_courses if c.isAIGenerated)
    manual_courses = len(all_courses) - ai_generated

    platform_stats = PlatformStatistics(
        totalUsers=len(all_users),
        activeUsers=len(active_users),
        totalCourses=len(all_courses),
        activeCourses=len(active_courses),
        archivedCourses=len(all_courses) - len(active_courses),
        totalModules=total_modules,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        totalEstimatedHours=total_estimated_hours,
        completedEstimatedHours=completed_estimated_hours,
        averageCourseProgress=average_course_progress,
        averageUserProgress=average_user_progress,
        usersByTier=dict(users_by_tier),
        coursesByDifficulty=dict(courses_by_difficulty),
        aiGeneratedCourses=ai_generated,
        manualCourses=manual_courses,
    )

    # Get top users by progress
    user_analytics = []
    for user in all_users:
        user_courses = await db.course.find_many(
            where={"userId": user.id},
            include={"modules": {"include": {"topics": True}}},
        )
        user_topics = [
            topic for course in user_courses for module in course.modules for topic in module.topics
        ]
        user_total_topics = len(user_topics)
        user_completed_topics = sum(1 for t in user_topics if t.completed)
        user_progress = (
            (user_completed_topics / user_total_topics * 100) if user_total_topics > 0 else 0.0
        )

        active_user_courses = [c for c in user_courses if not c.archived]
        completed_user_courses = sum(
            1
            for course in user_courses
            if len([t for m in course.modules for t in m.topics]) > 0
            and all(t.completed for m in course.modules for t in m.topics)
        )

        user_analytics.append(
            UserAnalyticsItem(
                userId=user.id,
                email=user.email,
                name=user.name,
                tier=str(user.tier),
                totalCourses=len(user_courses),
                activeCourses=len(active_user_courses),
                completedCourses=completed_user_courses,
                totalTopics=user_total_topics,
                completedTopics=user_completed_topics,
                overallProgress=user_progress,
                createdAt=user.createdAt.isoformat(),
            )
        )

    top_users = sorted(user_analytics, key=lambda x: x.overallProgress, reverse=True)[:10]

    # Get top courses by completion
    course_analytics = []
    for course in all_courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        course_total_topics = len(course_topics)
        course_completed_topics = sum(1 for t in course_topics if t.completed)
        course_progress = (
            (course_completed_topics / course_total_topics * 100)
            if course_total_topics > 0
            else 0.0
        )

        course_completed_modules = sum(
            1
            for module in course.modules
            if len(module.topics) > 0 and all(topic.completed for topic in module.topics)
        )

        course_analytics.append(
            CourseAnalyticsItem(
                courseId=course.id,
                title=course.title,
                userId=course.userId,
                userEmail=course.user.email,
                userName=course.user.name,
                progress=course_progress,
                totalTopics=course_total_topics,
                completedTopics=course_completed_topics,
                totalModules=len(course.modules),
                completedModules=course_completed_modules,
                difficulty=str(course.difficulty),
                isAIGenerated=course.isAIGenerated,
                isArchived=course.archived,
                createdAt=course.createdAt.isoformat(),
            )
        )

    top_courses = sorted(course_analytics, key=lambda x: x.progress, reverse=True)[:10]
    recent_courses = sorted(course_analytics, key=lambda x: x.createdAt, reverse=True)[:10]

    # User behavior metrics
    all_users = await db.user.find_many(where={"role": "USER"})
    active_users = [u for u in all_users if u.isActive]

    # Calculate DAU/MAU (simplified)
    now = datetime.utcnow()
    daily_active = len(set([u.id for u in active_users]))  # Simplified
    monthly_active = len(active_users)

    user_behavior = UserBehaviorMetrics(
        dailyActiveUsers=daily_active,
        monthlyActiveUsers=monthly_active,
        averageSessionLength=0.0,  # Would need to calculate from sessions
        averageSessionsPerUser=0.0,
        featureUsageRates={},
        userFlowAnalysis={},
    )

    # AI metrics
    all_messages = await db.chatmessage.find_many()
    total_tokens = sum(m.tokenCount or 0 for m in all_messages)
    voice_messages = [m for m in all_messages if m.audioUrl]

    ai_metrics = AIMetrics(
        totalRequests=len([m for m in all_messages if m.role == "USER"]),
        requestsByPeriod=[],
        totalTokensUsed=total_tokens,
        averageTokensPerRequest=total_tokens / len(all_messages) if all_messages else 0,
        voiceInteractions=len(voice_messages),
        voiceInteractionDuration=sum(m.duration or 0 for m in voice_messages) / 60,
        intentDetectionAccuracy=None,
        averageResponseTime=0.0,
    )

    # Retention metrics (simplified)
    retention = RetentionMetrics(
        retentionCohorts={},
        featureAdoptionRates={},
        timeToFirstValue=0.0,
        engagementScores={},
        reengagementPatterns={},
    )

    # Subscription funnel (simplified)
    free_users = [u for u in all_users if u.tier == "FREE"]
    premium_users = [u for u in all_users if u.tier in ["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]]

    subscription_funnel = SubscriptionFunnelMetrics(
        freeToPremiumConversionRate=len(premium_users) / len(all_users) * 100 if all_users else 0,
        trialConversionRate=None,
        upgradePromptEffectiveness=0.0,
        cancellationReasons={},
        averageLifetimeValue=0.0,
    )

    return EnhancedAdminAnalyticsResponse(
        platformStats=platform_stats,
        userBehavior=user_behavior,
        aiMetrics=ai_metrics,
        retention=retention,
        subscriptionFunnel=subscription_funnel,
        topUsers=top_users,
        topCourses=top_courses,
        recentCourses=recent_courses,
    )


# ============================================================================
# Achievement Unlocking Logic
# ============================================================================


async def _check_and_unlock_achievements(db: PrismaClient, user_id: str, session=None):
    """Check and unlock achievements based on milestones."""
    from ..models.analytics import AchievementType

    # Get user's current stats
    sessions = await db.studysession.find_many(
        where={"userId": user_id, "endTime": {"not": None}},
    )
    total_study_minutes = sum(s.duration or 0 for s in sessions)
    total_study_hours = total_study_minutes / 60

    courses = await db.course.find_many(
        where={"userId": user_id},
        include={"modules": {"include": {"topics": True}}},
    )
    completed_courses = sum(
        1
        for course in courses
        if len([t for m in course.modules for t in m.topics]) > 0
        and all(t.completed for m in course.modules for t in m.topics)
    )
    completed_topics = sum(
        1
        for course in courses
        for module in course.modules
        for topic in module.topics
        if topic.completed
    )

    goals = await db.goal.find_many(where={"userId": user_id})
    completed_goals = sum(1 for g in goals if g.status == "COMPLETED")

    messages = await db.chatmessage.find_many(where={"userId": user_id, "role": "USER"})
    total_messages = len(messages)

    streak = await db.userstreak.find_unique(where={"userId": user_id})
    current_streak = streak.currentStreak if streak else 0

    # Check existing achievements
    existing_achievements = await db.achievement.find_many(
        where={"userId": user_id},
    )
    unlocked_types = {a.achievementType for a in existing_achievements}

    # Define achievement milestones
    achievements_to_check = [
        # Study time milestones
        (
            AchievementType.STUDY_TIME_MILESTONE,
            10,
            "10 Hours Studied",
            "You've studied for 10 hours! 🎉",
            "📚",
        ),
        (
            AchievementType.STUDY_TIME_MILESTONE,
            50,
            "50 Hours Studied",
            "Halfway to 100 hours! Keep going! 💪",
            "📖",
        ),
        (
            AchievementType.STUDY_TIME_MILESTONE,
            100,
            "100 Hours Studied",
            "Century milestone! Amazing dedication! 🌟",
            "🏆",
        ),
        (
            AchievementType.STUDY_TIME_MILESTONE,
            500,
            "500 Hours Studied",
            "Incredible! You're a true scholar! 🎓",
            "👑",
        ),
        # Streak milestones
        (
            AchievementType.STREAK_MILESTONE,
            3,
            "3 Day Streak",
            "Great start! You're building a habit! 🔥",
            "🔥",
        ),
        (
            AchievementType.STREAK_MILESTONE,
            7,
            "7 Day Streak",
            "A full week! You're unstoppable! 💪",
            "🔥🔥",
        ),
        (
            AchievementType.STREAK_MILESTONE,
            30,
            "30 Day Streak",
            "A full month! Incredible consistency! 🌟",
            "🔥🔥🔥",
        ),
        # Course completion
        (
            AchievementType.COURSE_COMPLETION,
            1,
            "First Course Completed",
            "Congratulations on your first course! 🎉",
            "✅",
        ),
        (
            AchievementType.COURSE_COMPLETION,
            5,
            "5 Courses Completed",
            "You're on a roll! 5 courses down! 🚀",
            "⭐",
        ),
        (
            AchievementType.COURSE_COMPLETION,
            10,
            "10 Courses Completed",
            "Double digits! You're a learning machine! 🎓",
            "⭐⭐",
        ),
        # Topic completion
        (
            AchievementType.TOPIC_COMPLETION,
            10,
            "10 Topics Completed",
            "Great progress! Keep learning! 📚",
            "📝",
        ),
        (
            AchievementType.TOPIC_COMPLETION,
            50,
            "50 Topics Completed",
            "Halfway to 100! Amazing! 💪",
            "📚📚",
        ),
        (
            AchievementType.TOPIC_COMPLETION,
            100,
            "100 Topics Completed",
            "Century of knowledge! Outstanding! 🌟",
            "📚📚📚",
        ),
        # Goal achievement
        (
            AchievementType.GOAL_ACHIEVEMENT,
            5,
            "5 Goals Achieved",
            "You're achieving your dreams! 🎯",
            "🎯",
        ),
        (
            AchievementType.GOAL_ACHIEVEMENT,
            10,
            "10 Goals Achieved",
            "Double digit achievements! Amazing! 🏆",
            "🎯🎯",
        ),
        # AI usage
        (
            AchievementType.AI_USAGE,
            50,
            "50 AI Messages",
            "You're making great use of AI assistance! 🤖",
            "💬",
        ),
        (
            AchievementType.AI_USAGE,
            100,
            "100 AI Messages",
            "Century of AI interactions! 🚀",
            "💬💬",
        ),
        (
            AchievementType.AI_USAGE,
            500,
            "500 AI Messages",
            "AI power user! You're maximizing your learning! 🌟",
            "💬💬💬",
        ),
    ]

    # Check and unlock achievements
    for achievement_type, milestone_value, title, description, icon in achievements_to_check:
        if achievement_type in unlocked_types:
            continue  # Already unlocked

        should_unlock = False
        metadata = {"milestone": milestone_value}

        if achievement_type == AchievementType.STUDY_TIME_MILESTONE:
            should_unlock = total_study_hours >= milestone_value
        elif achievement_type == AchievementType.STREAK_MILESTONE:
            should_unlock = current_streak >= milestone_value
        elif achievement_type == AchievementType.COURSE_COMPLETION:
            should_unlock = completed_courses >= milestone_value
        elif achievement_type == AchievementType.TOPIC_COMPLETION:
            should_unlock = completed_topics >= milestone_value
        elif achievement_type == AchievementType.GOAL_ACHIEVEMENT:
            should_unlock = completed_goals >= milestone_value
        elif achievement_type == AchievementType.AI_USAGE:
            should_unlock = total_messages >= milestone_value

        if should_unlock:
            await db.achievement.create(
                data={
                    "userId": user_id,
                    "achievementType": achievement_type,
                    "title": title,
                    "description": description,
                    "icon": icon,
                    "metadata": metadata,
                }
            )
            unlocked_types.add(achievement_type)


# ============================================================================
# Report Generation with AI Insights
# ============================================================================


async def _generate_weekly_report(db: PrismaClient, user_id: str) -> WeeklyReport:
    """Generate weekly study report with AI insights."""
    from datetime import timezone
    from ..services.llm_service import GeminiService

    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)

    # Get study sessions for the week
    sessions = await db.studysession.find_many(
        where={
            "userId": user_id,
            "startTime": {"gte": week_start, "lte": week_end},
            "endTime": {"not": None},
        },
    )

    # Get completed topics
    courses = await db.course.find_many(
        where={"userId": user_id},
        include={"modules": {"include": {"topics": True}}},
    )

    topics_completed_this_week = []
    for course in courses:
        for module in course.modules:
            for topic in module.topics:
                if topic.completed and topic.updatedAt >= week_start:
                    topics_completed_this_week.append(topic)

    # Get completed goals
    goals = await db.goal.find_many(
        where={"userId": user_id, "status": "COMPLETED"},
    )
    goals_this_week = [g for g in goals if g.updatedAt >= week_start]

    # Get top courses by study time
    course_study_time = defaultdict(float)
    for session in sessions:
        if session.courseId:
            course_study_time[session.courseId] += session.duration or 0

    top_courses = sorted(course_study_time.items(), key=lambda x: x[1], reverse=True)[:3]
    top_course_titles = []
    for course_id, _ in top_courses:
        course = next((c for c in courses if c.id == course_id), None)
        if course:
            top_course_titles.append(course.title)

    # Check streak
    streak = await db.userstreak.find_unique(where={"userId": user_id})
    streak_maintained = False
    if streak and streak.lastStudyDate:
        try:
            last_study_date = (
                streak.lastStudyDate.date()
                if isinstance(streak.lastStudyDate, datetime)
                else streak.lastStudyDate
            )
            streak_maintained = (now.date() - last_study_date).days <= 7
        except Exception:
            streak_maintained = False

    # Generate AI insights
    llm_service = GeminiService()
    insights_prompt = f"""Based on the following weekly study data, generate 3-5 personalized insights and recommendations:

Total Study Time: {sum(s.duration or 0 for s in sessions) / 60:.1f} hours
Sessions Completed: {len(sessions)}
Topics Completed: {len(topics_completed_this_week)}
Goals Achieved: {len(goals_this_week)}
Streak Maintained: {streak_maintained}
Top Courses: {', '.join(top_course_titles) if top_course_titles else 'None'}

Generate insights that are:
1. Encouraging and motivational
2. Actionable (specific recommendations)
3. Personalized to the user's progress
4. Focused on improvement areas

Format as a JSON array of strings, each string being one insight/recommendation."""

    try:
        insights_response = await llm_service.get_chat_response([], insights_prompt)
        # Extract JSON array from response
        import json
        import re

        json_match = re.search(r"\[.*?\]", insights_response, re.DOTALL)
        if json_match:
            insights = json.loads(json_match.group(0))
        else:
            # Fallback: split by lines or common patterns
            insights = [
                line.strip()
                for line in insights_response.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ][:5]
    except Exception:
        insights = [
            "Keep up the great work!",
            "Try to maintain a consistent study schedule.",
            "Focus on completing one course at a time for better results.",
        ]

    return WeeklyReport(
        weekStart=week_start.date().isoformat(),
        weekEnd=week_end.date().isoformat(),
        totalStudyTime=sum(s.duration or 0 for s in sessions),
        sessionsCompleted=len(sessions),
        topicsCompleted=len(topics_completed_this_week),
        goalsAchieved=len(goals_this_week),
        streakMaintained=streak_maintained,
        topCourses=top_course_titles,
        insights=insights[:5],
    )


async def _generate_monthly_report(db: PrismaClient, user_id: str) -> MonthlyReport:
    """Generate monthly progress summary with AI insights."""
    from ..services.llm_service import GeminiService

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    month_end = (
        datetime(now.year, now.month + 1, 1) - timedelta(days=1)
        if now.month < 12
        else datetime(now.year + 1, 1, 1) - timedelta(days=1)
    )

    # Get study sessions for the month
    sessions = await db.studysession.find_many(
        where={
            "userId": user_id,
            "startTime": {"gte": month_start, "lte": month_end},
            "endTime": {"not": None},
        },
    )

    # Get completed courses
    courses = await db.course.find_many(
        where={"userId": user_id},
        include={"modules": {"include": {"topics": True}}},
    )
    courses_completed_this_month = [
        course
        for course in courses
        if len([t for m in course.modules for t in m.topics]) > 0
        and all(t.completed for m in course.modules for t in m.topics)
        and course.updatedAt >= month_start
    ]

    # Get completed topics
    topics_completed_this_month = []
    for course in courses:
        for module in course.modules:
            for topic in module.topics:
                if topic.completed and topic.updatedAt >= month_start:
                    topics_completed_this_month.append(topic)

    # Get completed goals
    goals = await db.goal.find_many(
        where={"userId": user_id, "status": "COMPLETED"},
    )
    goals_this_month = [g for g in goals if g.updatedAt >= month_start]

    # Get achievements unlocked this month
    achievements = await db.achievement.find_many(
        where={"userId": user_id, "unlockedAt": {"gte": month_start}},
    )

    # Calculate average streak
    streak = await db.userstreak.find_unique(where={"userId": user_id})
    average_streak = streak.currentStreak if streak else 0

    # Generate AI insights
    llm_service = GeminiService()
    insights_prompt = f"""Based on the following monthly study data, generate 3-5 comprehensive insights:

Total Study Time: {sum(s.duration or 0 for s in sessions) / 60:.1f} hours
Sessions Completed: {len(sessions)}
Courses Completed: {len(courses_completed_this_month)}
Topics Completed: {len(topics_completed_this_month)}
Goals Achieved: {len(goals_this_month)}
Achievements Unlocked: {len(achievements)}
Average Streak: {average_streak} days

Generate insights that:
1. Celebrate achievements
2. Identify patterns and trends
3. Provide strategic recommendations for next month
4. Are motivational and personalized

Format as a JSON array of strings."""

    try:
        insights_response = await llm_service.get_chat_response([], insights_prompt)
        import json
        import re

        json_match = re.search(r"\[.*?\]", insights_response, re.DOTALL)
        if json_match:
            insights = json.loads(json_match.group(0))
        else:
            insights = [
                line.strip()
                for line in insights_response.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ][:5]
    except Exception:
        insights = [
            "Excellent progress this month!",
            "Consider setting new goals for next month.",
            "Your consistency is paying off!",
        ]

    return MonthlyReport(
        month=now.strftime("%B"),
        year=now.year,
        totalStudyTime=sum(s.duration or 0 for s in sessions),
        sessionsCompleted=len(sessions),
        coursesCompleted=len(courses_completed_this_month),
        topicsCompleted=len(topics_completed_this_month),
        goalsAchieved=len(goals_this_month),
        averageStreak=average_streak,
        achievementsUnlocked=len(achievements),
        insights=insights[:5],
    )
