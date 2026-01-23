"""
Analytics models for progress tracking and statistics.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

# ============================================================================
# User Analytics Models
# ============================================================================


class UserProgressSummary(BaseModel):
    """Summary of user's overall progress across all courses."""

    userId: str
    totalCourses: int
    activeCourses: int
    completedCourses: int
    archivedCourses: int
    totalModules: int
    completedModules: int
    totalTopics: int
    completedTopics: int
    overallProgress: float  # Weighted average across all courses
    totalEstimatedHours: float
    completedEstimatedHours: float
    averageCourseProgress: float


class CourseProgressItem(BaseModel):
    """Progress details for a single course in user analytics."""

    courseId: str
    title: str
    progress: float
    totalTopics: int
    completedTopics: int
    totalModules: int
    completedModules: int
    isArchived: bool
    createdAt: str


class UserAnalyticsResponse(BaseModel):
    """Complete user analytics response."""

    summary: UserProgressSummary
    courses: list[CourseProgressItem]


# ============================================================================
# Admin Analytics Models
# ============================================================================


class PlatformStatistics(BaseModel):
    """Platform-wide statistics."""

    totalUsers: int
    activeUsers: int
    totalCourses: int
    activeCourses: int
    archivedCourses: int
    totalModules: int
    totalTopics: int
    completedTopics: int
    totalEstimatedHours: float
    completedEstimatedHours: float
    averageCourseProgress: float
    averageUserProgress: float
    usersByTier: dict[str, int]  # {"FREE": 100, "PREMIUM_MONTHLY": 50, ...}
    coursesByDifficulty: dict[str, int]  # {"BEGINNER": 50, "INTERMEDIATE": 30, ...}
    aiGeneratedCourses: int
    manualCourses: int


class UserAnalyticsItem(BaseModel):
    """Analytics for a single user (admin view)."""

    userId: str
    email: str
    name: str | None
    tier: str
    totalCourses: int
    activeCourses: int
    completedCourses: int
    totalTopics: int
    completedTopics: int
    overallProgress: float
    createdAt: str


class CourseAnalyticsItem(BaseModel):
    """Analytics for a single course (admin view)."""

    courseId: str
    title: str
    userId: str
    userEmail: str
    userName: str | None
    progress: float
    totalTopics: int
    completedTopics: int
    totalModules: int
    completedModules: int
    difficulty: str
    isAIGenerated: bool
    isArchived: bool
    createdAt: str


class AdminAnalyticsResponse(BaseModel):
    """Complete admin analytics response."""

    platformStats: PlatformStatistics
    topUsers: list[UserAnalyticsItem]  # Top users by progress
    topCourses: list[CourseAnalyticsItem]  # Top courses by completion
    recentCourses: list[CourseAnalyticsItem]  # Recently created courses


class UserDetailAnalyticsResponse(BaseModel):
    """Detailed analytics for a specific user (admin view)."""

    user: UserAnalyticsItem
    courses: list[CourseAnalyticsItem]
    summary: UserProgressSummary


# ============================================================================
# Study Analytics Models
# ============================================================================


class StudyTimePeriod(BaseModel):
    """Study time for a specific period."""

    date: str  # ISO date string
    minutes: float
    sessions: int


class StudyTimeByCourse(BaseModel):
    """Study time breakdown by course."""

    courseId: str
    courseTitle: str
    totalMinutes: float
    sessionCount: int
    averageSessionDuration: float


class StudyTimeBySubject(BaseModel):
    """Study time breakdown by subject (derived from course difficulty or tags)."""

    subject: str
    totalMinutes: float
    sessionCount: int


class StudyStreak(BaseModel):
    """User's study streak information."""

    currentStreak: int
    longestStreak: int
    lastStudyDate: str | None


class ProductiveTimeSlot(BaseModel):
    """Most productive time of day."""

    hour: int  # 0-23
    totalMinutes: float
    sessionCount: int


class SessionDurationStats(BaseModel):
    """Session duration statistics."""

    average: float
    median: float
    min: float
    max: float
    totalSessions: int


class StudyAnalytics(BaseModel):
    """Comprehensive study analytics."""

    # Time periods
    daily: list[StudyTimePeriod]
    weekly: list[StudyTimePeriod]
    monthly: list[StudyTimePeriod]

    # Breakdowns
    byCourse: list[StudyTimeByCourse]
    bySubject: list[StudyTimeBySubject]

    # Streak
    streak: StudyStreak

    # Productivity
    productiveTimes: list[ProductiveTimeSlot]

    # Session stats
    sessionStats: SessionDurationStats


# ============================================================================
# Progress Analytics Models
# ============================================================================


class CompletionRate(BaseModel):
    """Completion rate over time."""

    date: str
    rate: float  # 0-100


class GoalAchievementRate(BaseModel):
    """Goal achievement statistics."""

    totalGoals: int
    completedGoals: int
    activeGoals: int
    achievementRate: float  # Percentage
    averageProgress: float


class TaskCompletionTrend(BaseModel):
    """Task completion over time."""

    date: str
    completed: int
    total: int


class ScheduleAdherence(BaseModel):
    """Schedule adherence statistics."""

    totalScheduledBlocks: int
    completedBlocks: int
    adherenceRate: float  # Percentage
    averageDeviationMinutes: float  # How far off from scheduled time


class LearningPaceTrend(BaseModel):
    """Learning pace trends."""

    date: str
    topicsCompleted: int
    averageTimePerTopic: float  # Minutes


class ProgressAnalytics(BaseModel):
    """Comprehensive progress analytics."""

    courseCompletionRates: list[CompletionRate]
    goalAchievementRate: GoalAchievementRate
    taskCompletionTrends: list[TaskCompletionTrend]
    scheduleAdherence: ScheduleAdherence
    learningPaceTrends: list[LearningPaceTrend]


# ============================================================================
# AI Usage Analytics Models
# ============================================================================


class AIMessageStats(BaseModel):
    """AI message statistics."""

    totalMessages: int
    messagesByPeriod: list[StudyTimePeriod]  # Reusing StudyTimePeriod for time series
    averageMessagesPerDay: float


class VoiceInteractionStats(BaseModel):
    """Voice interaction statistics."""

    totalInteractions: int
    totalDuration: float  # Minutes
    averageDuration: float  # Minutes per interaction
    interactionsByPeriod: list[StudyTimePeriod]


class AIFeatureUsage(BaseModel):
    """AI feature usage statistics."""

    featureName: str
    usageCount: int
    lastUsed: str | None


class AIGeneratedContentStats(BaseModel):
    """AI-generated content statistics."""

    coursesGenerated: int
    notesGenerated: int
    summariesGenerated: int
    totalTokensUsed: int


class AIUsageAnalytics(BaseModel):
    """Comprehensive AI usage analytics."""

    messageStats: AIMessageStats
    voiceInteractionStats: VoiceInteractionStats
    featureUsage: list[AIFeatureUsage]
    generatedContentStats: AIGeneratedContentStats


# ============================================================================
# Insights & Reports Models
# ============================================================================


class WeeklyReport(BaseModel):
    """Weekly study report."""

    weekStart: str
    weekEnd: str
    totalStudyTime: float  # Minutes
    sessionsCompleted: int
    topicsCompleted: int
    goalsAchieved: int
    streakMaintained: bool
    topCourses: list[str]
    insights: list[str]  # Personalized insights


class MonthlyReport(BaseModel):
    """Monthly progress summary."""

    month: str
    year: int
    totalStudyTime: float  # Minutes
    sessionsCompleted: int
    coursesCompleted: int
    topicsCompleted: int
    goalsAchieved: int
    averageStreak: float
    achievementsUnlocked: int
    insights: list[str]


class Recommendation(BaseModel):
    """Personalized recommendation."""

    type: Literal["course", "goal", "schedule", "study_time", "topic"]
    title: str
    description: str
    priority: Literal["high", "medium", "low"]


class AchievementBadge(BaseModel):
    """Achievement badge/milestone."""

    id: str
    type: str
    title: str
    description: str
    icon: str | None
    unlockedAt: str
    metadata: dict | None


class GoalComparison(BaseModel):
    """Comparison to goals."""

    goalId: str | None
    goalTitle: str
    targetValue: float
    currentValue: float
    progress: float  # Percentage
    status: Literal["on_track", "behind", "ahead", "completed"]


class InsightsAndReports(BaseModel):
    """Comprehensive insights and reports."""

    weeklyReport: WeeklyReport | None
    monthlyReport: MonthlyReport | None
    recommendations: list[Recommendation]
    achievements: list[AchievementBadge]
    goalComparisons: list[GoalComparison]


# ============================================================================
# Enhanced User Analytics Response
# ============================================================================


class EnhancedUserAnalyticsResponse(BaseModel):
    """Complete enhanced user analytics response."""

    summary: UserProgressSummary
    courses: list[CourseProgressItem]
    studyAnalytics: StudyAnalytics
    progressAnalytics: ProgressAnalytics
    aiUsageAnalytics: AIUsageAnalytics
    insightsAndReports: InsightsAndReports


# ============================================================================
# Admin Analytics Models (Enhanced)
# ============================================================================


class UserBehaviorMetrics(BaseModel):
    """User behavior metrics for admin."""

    dailyActiveUsers: int
    monthlyActiveUsers: int
    averageSessionLength: float  # Minutes
    averageSessionsPerUser: float
    featureUsageRates: dict[str, float]  # Feature name -> usage rate
    userFlowAnalysis: dict[str, int]  # Flow step -> user count


class AIMetrics(BaseModel):
    """AI metrics for admin."""

    totalRequests: int
    requestsByPeriod: list[StudyTimePeriod]
    totalTokensUsed: int
    averageTokensPerRequest: float
    voiceInteractions: int
    voiceInteractionDuration: float  # Minutes
    intentDetectionAccuracy: float | None  # If tracked
    averageResponseTime: float  # Seconds


class RetentionMetrics(BaseModel):
    """Retention and engagement metrics."""

    retentionCohorts: dict[str, float]  # Cohort -> retention rate
    featureAdoptionRates: dict[str, float]  # Feature -> adoption rate
    timeToFirstValue: float  # Days
    engagementScores: dict[str, float]  # User ID -> engagement score
    reengagementPatterns: dict[str, int]  # Pattern -> count


class SubscriptionFunnelMetrics(BaseModel):
    """Subscription funnel metrics."""

    freeToPremiumConversionRate: float
    trialConversionRate: float | None
    upgradePromptEffectiveness: float
    cancellationReasons: dict[str, int]
    averageLifetimeValue: float  # In dollars


class EnhancedAdminAnalyticsResponse(BaseModel):
    """Enhanced admin analytics response."""

    platformStats: PlatformStatistics
    userBehavior: UserBehaviorMetrics
    aiMetrics: AIMetrics
    retention: RetentionMetrics
    subscriptionFunnel: SubscriptionFunnelMetrics
    topUsers: list[UserAnalyticsItem]
    topCourses: list[CourseAnalyticsItem]
    recentCourses: list[CourseAnalyticsItem]


# ============================================================================
# Dashboard Models
# ============================================================================


class DashboardStats(BaseModel):
    """Quick stats for dashboard overview."""

    totalCourses: int
    activeCourses: int
    completedCourses: int
    totalGoals: int
    activeGoals: int
    completedGoals: int
    totalStudyMinutes: float  # Total study time in minutes
    currentStreak: int
    longestStreak: int
    upcomingSchedulesCount: int  # Schedules in next 7 days


class DashboardCourseItem(BaseModel):
    """Recent course item for dashboard."""

    courseId: str
    title: str
    progress: float
    totalTopics: int
    completedTopics: int
    createdAt: str


class DashboardGoalItem(BaseModel):
    """Active goal item for dashboard."""

    goalId: str
    title: str
    description: str | None
    progress: float
    targetDate: str | None
    status: str
    createdAt: str


class DashboardScheduleItem(BaseModel):
    """Upcoming schedule item for dashboard."""

    scheduleId: str
    title: str
    description: str | None
    startAt: str
    endAt: str
    courseId: str | None
    topicId: str | None
    goalId: str | None


class DashboardResponse(BaseModel):
    """Complete dashboard response with aggregated data."""

    stats: DashboardStats
    recentCourses: list[DashboardCourseItem]  # Latest 5 courses
    activeGoals: list[DashboardGoalItem]  # Active goals (limit 5)
    upcomingSchedules: list[DashboardScheduleItem]  # Next 7 days
    dailyGoalProgress: float | None  # Progress towards daily study goal (0-100)
