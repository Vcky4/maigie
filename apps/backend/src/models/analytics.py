"""
Analytics models for progress tracking and statistics.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from typing import Optional

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
    name: Optional[str]
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
    userName: Optional[str]
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
