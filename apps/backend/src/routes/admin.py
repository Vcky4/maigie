"""
Admin routes for user management.

This module handles admin-only operations including:
- Listing all users
- Viewing user details
- Updating user information
- Deleting users
- Managing user roles and status

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from prisma import Prisma
from prisma.models import User

from ..core.security import get_password_hash
from ..dependencies import AdminUser, DBDep
from ..models.analytics import (
    AdminAnalyticsResponse,
    CourseAnalyticsItem,
    PlatformStatistics,
    UserAnalyticsItem,
    UserDetailAnalyticsResponse,
    UserProgressSummary,
)
from ..services.credit_service import initialize_user_credits
from ..services.email import send_bulk_email
from ..services.subscription_service import update_user_subscription_from_stripe
from ..services.audit_service import log_admin_action
from ..utils.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ==========================================
#  DASHBOARD ENDPOINT
# ==========================================


@router.get("/dashboard", response_model=dict)
async def get_dashboard_stats(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get dashboard statistics overview.

    Only accessible by admin users.
    """
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)

    # User statistics
    total_users = await db.user.count()
    active_users = await db.user.count(where={"isActive": True})
    new_users_30d = await db.user.count(
        where={"createdAt": {"gte": thirty_days_ago}, "role": "USER"}
    )
    new_users_7d = await db.user.count(where={"createdAt": {"gte": seven_days_ago}, "role": "USER"})

    # Subscription statistics
    premium_users = await db.user.count(
        where={"tier": {"in": ["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]}}
    )
    free_users = await db.user.count(where={"tier": "FREE"})

    # Chat statistics
    total_sessions = await db.chatsession.count()
    total_messages = await db.chatmessage.count()
    recent_messages = await db.chatmessage.find_many(where={"createdAt": {"gte": thirty_days_ago}})
    total_tokens = sum(msg.tokenCount or 0 for msg in recent_messages)
    total_cost_usd = sum(msg.costUsd or 0.0 for msg in recent_messages)
    total_revenue_usd = sum(msg.revenueUsd or 0.0 for msg in recent_messages)
    total_profit_usd = total_revenue_usd - total_cost_usd

    # Course statistics
    total_courses = await db.course.count()
    ai_courses = await db.course.count(where={"isAIGenerated": True})
    active_courses = await db.course.count(where={"archived": False})

    # Feedback statistics
    pending_feedback = await db.feedback.count(where={"status": "PENDING"})
    total_feedback = await db.feedback.count()

    # Revenue statistics (from subscriptions)
    premium_monthly_users = await db.user.count(
        where={"tier": "PREMIUM_MONTHLY", "stripeSubscriptionStatus": "active"}
    )
    premium_yearly_users = await db.user.count(
        where={"tier": "PREMIUM_YEARLY", "stripeSubscriptionStatus": "active"}
    )
    # Estimate MRR (Monthly Recurring Revenue)
    # Assuming $10/month for monthly and $100/year for yearly
    estimated_mrr = (premium_monthly_users * 10) + (premium_yearly_users * 100 / 12)

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "newLast30Days": new_users_30d,
            "newLast7Days": new_users_7d,
            "premium": premium_users,
            "free": free_users,
        },
        "subscriptions": {
            "premiumMonthly": premium_monthly_users,
            "premiumYearly": premium_yearly_users,
            "estimatedMRR": round(estimated_mrr, 2),
        },
        "chat": {
            "totalSessions": total_sessions,
            "totalMessages": total_messages,
            "totalTokensLast30Days": total_tokens,
            "totalCostUsdLast30Days": round(total_cost_usd, 4),
            "totalRevenueUsdLast30Days": round(total_revenue_usd, 4),
            "totalProfitUsdLast30Days": round(total_profit_usd, 4),
            "profitMargin": round(
                (total_profit_usd / total_revenue_usd * 100) if total_revenue_usd > 0 else 0.0, 2
            ),
        },
        "courses": {
            "total": total_courses,
            "aiGenerated": ai_courses,
            "active": active_courses,
        },
        "feedback": {
            "total": total_feedback,
            "pending": pending_feedback,
        },
    }


# ==========================================
#  REQUEST/RESPONSE MODELS
# ==========================================


class UserCreateRequest(BaseModel):
    """Request model for creating a new user."""

    email: EmailStr
    name: str | None = None
    password: str | None = Field(
        None, min_length=8, description="Password (optional, min 8 characters)"
    )
    tier: str = Field("FREE", description="User tier")
    role: str = Field("USER", description="User role")
    isActive: bool = Field(True, description="Whether user is active")
    isOnboarded: bool = Field(False, description="Whether user has completed onboarding")


class UserUpdateRequest(BaseModel):
    """Request model for updating user information."""

    name: str | None = None
    email: EmailStr | None = None
    tier: str | None = None
    role: str | None = None
    isActive: bool | None = None
    isOnboarded: bool | None = None


class UserListResponse(BaseModel):
    """Response model for user list."""

    users: list[dict]
    total: int
    page: int
    pageSize: int
    totalPages: int


class UserDetailResponse(BaseModel):
    """Response model for user details."""

    id: str
    email: str
    name: str | None
    tier: str
    role: str
    isActive: bool
    isOnboarded: bool
    provider: str | None
    stripeCustomerId: str | None
    stripeSubscriptionStatus: str | None
    creditsUsed: int
    creditsHardCap: int | None
    creditsUsedToday: int | None
    creditsDailyLimit: int | None
    createdAt: str
    updatedAt: str


class BulkEmailRequest(BaseModel):
    """Request model for sending bulk emails."""

    subject: str = Field(..., min_length=1, max_length=200, description="Email subject")
    content: str = Field(..., min_length=1, description="HTML email content")
    filterActive: bool | None = Field(
        None, description="Filter by active status (None = all users)"
    )
    filterTier: str | None = Field(None, description="Filter by tier (None = all tiers)")


class BulkEmailResponse(BaseModel):
    """Response model for bulk email operation."""

    message: str
    totalUsers: int
    emailsSent: int
    emailsFailed: int
    failedEmails: list[str] = []


# ==========================================
#  USER MANAGEMENT ENDPOINTS
# ==========================================


@router.post("/users", response_model=UserDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreateRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Create a new user account.

    Only accessible by admin users.
    """
    # Check if email already exists
    existing_user = await db.user.find_unique(where={"email": user_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Validate tier
    valid_tiers = ["FREE", "PREMIUM_MONTHLY", "PREMIUM_YEARLY"]
    tier_upper = user_data.tier.upper()
    if tier_upper not in valid_tiers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tier. Must be one of: {', '.join(valid_tiers)}",
        )

    # Validate role
    valid_roles = ["USER", "ADMIN"]
    role_upper = user_data.role.upper()
    if role_upper not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}",
        )

    # Hash password if provided
    password_hash = None
    if user_data.password:
        password_hash = get_password_hash(user_data.password)

    # Create user
    new_user = await db.user.create(
        data={
            "email": user_data.email,
            "name": user_data.name,
            "passwordHash": password_hash,
            "provider": "email" if password_hash else None,
            "tier": tier_upper,
            "role": role_upper,
            "isActive": user_data.isActive,
            "isOnboarded": user_data.isOnboarded,
            "preferences": {
                "create": {
                    "theme": "light",
                    "language": "en",
                    "notifications": True,
                }
            },
        },
        include={"preferences": True},
    )

    # Initialize credits for the user
    new_user = await initialize_user_credits(new_user)

    logger.info(f"Admin {admin_user.email} created user {new_user.id} ({new_user.email})")

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "create_user",
        "user",
        new_user.id,
        {"user_email": new_user.email, "tier": new_user.tier, "role": new_user.role},
        db,
    )

    return {
        "id": new_user.id,
        "email": new_user.email,
        "name": new_user.name,
        "tier": str(new_user.tier),
        "role": str(new_user.role),
        "isActive": new_user.isActive,
        "isOnboarded": new_user.isOnboarded,
        "provider": new_user.provider,
        "stripeCustomerId": new_user.stripeCustomerId,
        "stripeSubscriptionStatus": new_user.stripeSubscriptionStatus,
        "creditsUsed": new_user.creditsUsed or 0,
        "creditsHardCap": new_user.creditsHardCap,
        "creditsUsedToday": new_user.creditsUsedToday,
        "creditsDailyLimit": new_user.creditsDailyLimit,
        "createdAt": new_user.createdAt.isoformat(),
        "updatedAt": new_user.updatedAt.isoformat(),
    }


@router.get("/users", response_model=UserListResponse)
async def list_users(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by email or name"),
    tier: str | None = Query(None, description="Filter by tier"),
    role: str | None = Query(None, description="Filter by role"),
    isActive: bool | None = Query(None, description="Filter by active status"),
):
    """
    List all users with pagination and filtering.

    Only accessible by admin users.
    """
    # Build where clause
    where: dict = {}

    if search:
        where["OR"] = [
            {"email": {"contains": search, "mode": "insensitive"}},
            {"name": {"contains": search, "mode": "insensitive"}},
        ]

    if tier:
        where["tier"] = tier.upper()

    if role:
        where["role"] = role.upper()

    if isActive is not None:
        where["isActive"] = isActive

    # Count total matching users
    total = await db.user.count(where=where)

    # Calculate pagination
    skip = (page - 1) * pageSize
    total_pages = (total + pageSize - 1) // pageSize

    # Fetch paginated users
    users = await db.user.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"createdAt": "desc"},
        include={"preferences": True},
    )

    # Format users for response
    user_list = []
    for user in users:
        user_list.append(
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "tier": str(user.tier),
                "role": str(user.role),
                "isActive": user.isActive,
                "isOnboarded": user.isOnboarded,
                "creditsUsed": user.creditsUsed or 0,
                "creditsHardCap": user.creditsHardCap,
                "creditsSoftCap": user.creditsSoftCap,
                "creditsUsedToday": user.creditsUsedToday or 0,
                "creditsDailyLimit": user.creditsDailyLimit,
                "createdAt": user.createdAt.isoformat(),
                "updatedAt": user.updatedAt.isoformat(),
            }
        )

    return {
        "users": user_list,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_details(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get detailed information about a specific user.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(
        where={"id": user_id},
        include={"preferences": True},
    )

    if not user:
        raise ResourceNotFoundError("User", user_id)

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "tier": str(user.tier),
        "role": str(user.role),
        "isActive": user.isActive,
        "isOnboarded": user.isOnboarded,
        "provider": user.provider,
        "stripeCustomerId": user.stripeCustomerId,
        "stripeSubscriptionStatus": user.stripeSubscriptionStatus,
        "creditsUsed": user.creditsUsed or 0,
        "creditsHardCap": user.creditsHardCap,
        "creditsUsedToday": user.creditsUsedToday,
        "creditsDailyLimit": user.creditsDailyLimit,
        "createdAt": user.createdAt.isoformat(),
        "updatedAt": user.updatedAt.isoformat(),
    }


@router.put("/users/{user_id}", response_model=UserDetailResponse)
async def update_user(
    user_id: str,
    update_data: UserUpdateRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Update user information.

    Only accessible by admin users.
    """
    # Check if user exists
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from removing their own admin role
    if user_id == admin_user.id and update_data.role and update_data.role.upper() != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin role",
        )

    # Build update data
    update_dict: dict = {}
    if update_data.name is not None:
        update_dict["name"] = update_data.name
    if update_data.email is not None:
        # Check if email already exists for another user
        existing_user = await db.user.find_unique(where={"email": update_data.email})
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )
        update_dict["email"] = update_data.email
    if update_data.tier is not None:
        update_dict["tier"] = update_data.tier.upper()
    if update_data.role is not None:
        update_dict["role"] = update_data.role.upper()
    if update_data.isActive is not None:
        update_dict["isActive"] = update_data.isActive
    if update_data.isOnboarded is not None:
        update_dict["isOnboarded"] = update_data.isOnboarded

    # Update user
    updated_user = await db.user.update(
        where={"id": user_id},
        data=update_dict,
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} updated user {user_id}")

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "update_user",
        "user",
        user_id,
        {"updated_fields": list(update_dict.keys())},
        db,
    )

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Delete a user permanently.

    Only accessible by admin users.
    """
    # Check if user exists
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from deleting themselves
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # Delete user (cascade will handle related records)
    await db.user.delete(where={"id": user_id})

    logger.info(f"Admin {admin_user.email} deleted user {user_id}")

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "delete_user",
        "user",
        user_id,
        {"deleted_user_email": user.email},
        db,
    )

    return None


@router.post("/users/{user_id}/activate", response_model=UserDetailResponse)
async def activate_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Activate a user account.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"isActive": True},
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} activated user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.post("/users/{user_id}/deactivate", response_model=UserDetailResponse)
async def deactivate_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Deactivate a user account.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from deactivating themselves
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"isActive": False},
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} deactivated user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


# ============================================================================
# Analytics & Progress Tracking Endpoints
# ============================================================================


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_platform_analytics(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get comprehensive platform-wide analytics and statistics.

    Only accessible by admin users.
    """
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
        if len(user_topics) > 0:
            completed = sum(1 for t in user_topics if t.completed)
            progress = (completed / len(user_topics)) * 100
            user_progresses.append(progress)

    average_user_progress = sum(user_progresses) / len(user_progresses) if user_progresses else 0.0

    # Users by tier
    users_by_tier = {}
    for user in all_users:
        tier = str(user.tier)
        users_by_tier[tier] = users_by_tier.get(tier, 0) + 1

    # Courses by difficulty
    courses_by_difficulty = {}
    for course in all_courses:
        difficulty = str(course.difficulty)
        courses_by_difficulty[difficulty] = courses_by_difficulty.get(difficulty, 0) + 1

    # AI vs Manual courses
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
        usersByTier=users_by_tier,
        coursesByDifficulty=courses_by_difficulty,
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

    # Sort by progress (descending) and take top 10
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

        # Count completed modules
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

    # Sort courses by progress and take top 10
    top_courses = sorted(
        course_analytics,
        key=lambda x: x.progress,
        reverse=True,
    )[:10]

    # Get recent courses (last 10)
    recent_courses = sorted(
        course_analytics,
        key=lambda x: x.createdAt,
        reverse=True,
    )[:10]

    return AdminAnalyticsResponse(
        platformStats=platform_stats,
        topUsers=top_users,
        topCourses=top_courses,
        recentCourses=recent_courses,
    )


@router.get("/analytics/users/{user_id}", response_model=UserDetailAnalyticsResponse)
async def get_user_analytics(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get detailed analytics for a specific user.

    Only accessible by admin users.
    """
    # Check if user exists
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Get all user courses with modules and topics
    courses = await db.course.find_many(
        where={"userId": user_id},
        include={"modules": {"include": {"topics": True}}},
        order={"createdAt": "desc"},
    )

    # Calculate user statistics
    total_courses = len(courses)
    active_courses = [c for c in courses if not c.archived]
    archived_courses = [c for c in courses if c.archived]

    total_modules = sum(len(c.modules) for c in courses)
    total_topics = sum(len(module.topics) for c in courses for module in c.modules)
    completed_topics = sum(
        1 for c in courses for module in c.modules for topic in module.topics if topic.completed
    )

    # Calculate completed modules
    completed_modules = 0
    for course in courses:
        for module in course.modules:
            if len(module.topics) > 0 and all(topic.completed for topic in module.topics):
                completed_modules += 1

    # Calculate completed courses
    completed_courses = 0
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0 and all(topic.completed for topic in course_topics):
            completed_courses += 1

    # Calculate estimated hours
    total_estimated_hours = 0.0
    completed_estimated_hours = 0.0
    for course in courses:
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
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        if len(course_topics) > 0:
            completed = sum(1 for t in course_topics if t.completed)
            progress = (completed / len(course_topics)) * 100
            course_progresses.append(progress)

    average_course_progress = (
        sum(course_progresses) / len(course_progresses) if course_progresses else 0.0
    )

    # Build user analytics item
    user_analytics_item = UserAnalyticsItem(
        userId=user.id,
        email=user.email,
        name=user.name,
        tier=str(user.tier),
        totalCourses=total_courses,
        activeCourses=len(active_courses),
        completedCourses=completed_courses,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        overallProgress=overall_progress,
        createdAt=user.createdAt.isoformat(),
    )

    # Build summary
    summary = UserProgressSummary(
        userId=user.id,
        totalCourses=total_courses,
        activeCourses=len(active_courses),
        completedCourses=completed_courses,
        archivedCourses=len(archived_courses),
        totalModules=total_modules,
        completedModules=completed_modules,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        overallProgress=overall_progress,
        totalEstimatedHours=total_estimated_hours,
        completedEstimatedHours=completed_estimated_hours,
        averageCourseProgress=average_course_progress,
    )

    # Build course analytics items
    course_items = []
    for course in courses:
        course_topics = [topic for module in course.modules for topic in module.topics]
        course_total_topics = len(course_topics)
        course_completed_topics = sum(1 for t in course_topics if t.completed)
        course_progress = (
            (course_completed_topics / course_total_topics * 100)
            if course_total_topics > 0
            else 0.0
        )

        # Count completed modules
        course_completed_modules = sum(
            1
            for module in course.modules
            if len(module.topics) > 0 and all(topic.completed for topic in module.topics)
        )

        course_items.append(
            CourseAnalyticsItem(
                courseId=course.id,
                title=course.title,
                userId=course.userId,
                userEmail=user.email,
                userName=user.name,
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

    return UserDetailAnalyticsResponse(
        user=user_analytics_item,
        courses=course_items,
        summary=summary,
    )


@router.get("/users/{user_id}/summary", response_model=dict)
async def get_user_summary(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get comprehensive user summary including stats, costs, revenue, etc.

    Only accessible by admin users.
    """
    # Get user
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Get chat statistics
    messages = await db.chatmessage.find_many(where={"userId": user_id})
    total_messages = len(messages)
    total_tokens = sum(msg.tokenCount or 0 for msg in messages)
    total_cost_usd = sum(msg.costUsd or 0.0 for msg in messages)
    total_revenue_usd = sum(msg.revenueUsd or 0.0 for msg in messages)

    # Get course count
    total_courses = await db.course.count(where={"userId": user_id})

    # Get referral statistics
    referral_rewards = await db.referralreward.find_many(where={"referrerId": user_id})
    total_referrals = len(referral_rewards)
    claimed_referrals = sum(1 for r in referral_rewards if r.isClaimed)

    # Get sessions count
    total_sessions = await db.chatsession.count(where={"userId": user_id})

    return {
        "userId": user.id,
        "email": user.email,
        "name": user.name,
        "tier": str(user.tier),
        "role": str(user.role),
        "isActive": user.isActive,
        "subscription": {
            "status": user.stripeSubscriptionStatus,
            "customerId": user.stripeCustomerId,
            "subscriptionId": user.stripeSubscriptionId,
            "periodStart": (
                user.subscriptionCurrentPeriodStart.isoformat()
                if user.subscriptionCurrentPeriodStart
                else None
            ),
            "periodEnd": (
                user.subscriptionCurrentPeriodEnd.isoformat()
                if user.subscriptionCurrentPeriodEnd
                else None
            ),
        },
        "credits": {
            "used": user.creditsUsed or 0,
            "hardCap": user.creditsHardCap,
            "softCap": user.creditsSoftCap,
            "usedToday": user.creditsUsedToday or 0,
            "dailyLimit": user.creditsDailyLimit,
            "periodStart": user.creditsPeriodStart.isoformat() if user.creditsPeriodStart else None,
            "periodEnd": user.creditsPeriodEnd.isoformat() if user.creditsPeriodEnd else None,
        },
        "statistics": {
            "totalMessages": total_messages,
            "totalTokens": total_tokens,
            "totalSessions": total_sessions,
            "totalCourses": total_courses,
            "totalCostUsd": round(total_cost_usd, 4),
            "totalRevenueUsd": round(total_revenue_usd, 4),
            "totalProfitUsd": round(total_revenue_usd - total_cost_usd, 4),
            "totalReferrals": total_referrals,
            "claimedReferrals": claimed_referrals,
        },
        "createdAt": user.createdAt.isoformat(),
    }


# ============================================================================
# Bulk Email Endpoint
# ============================================================================


@router.post("/bulk-email", response_model=BulkEmailResponse)
async def send_bulk_emails(
    email_data: BulkEmailRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Send bulk emails to all users (with optional filtering).

    Only accessible by admin users.
    """
    # Build where clause for filtering users
    where: dict = {"role": "USER"}  # Only send to regular users, not admins

    if email_data.filterActive is not None:
        where["isActive"] = email_data.filterActive

    if email_data.filterTier:
        where["tier"] = email_data.filterTier.upper()

    # Get all matching users
    users = await db.user.find_many(where=where)

    if not users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No users found matching the specified filters",
        )

    total_users = len(users)
    emails_sent = 0
    emails_failed = 0
    failed_emails: list[str] = []

    logger.info(
        f"Admin {admin_user.email} sending bulk email to {total_users} users. "
        f"Subject: {email_data.subject}"
    )

    # Send emails to all users
    for user in users:
        try:
            await send_bulk_email(
                email=user.email,
                name=user.name,
                subject=email_data.subject,
                content=email_data.content,
            )
            emails_sent += 1
        except Exception as e:
            emails_failed += 1
            failed_emails.append(user.email)
            logger.error(f"Failed to send bulk email to {user.email}: {e}")

    logger.info(
        f"Bulk email completed: {emails_sent} sent, {emails_failed} failed out of {total_users} total"
    )

    return BulkEmailResponse(
        message=f"Bulk email sent to {emails_sent} users. {emails_failed} failed.",
        totalUsers=total_users,
        emailsSent=emails_sent,
        emailsFailed=emails_failed,
        failedEmails=failed_emails,
    )


# ============================================================================
# Credit Management Endpoints
# ============================================================================


class CreditAdjustRequest(BaseModel):
    """Request model for adjusting user credits."""

    credits: int = Field(
        ..., description="Amount to adjust (positive to add, negative to subtract)"
    )
    reason: str | None = Field(None, max_length=500, description="Reason for adjustment")


class CreditLimitUpdateRequest(BaseModel):
    """Request model for updating credit limits."""

    creditsHardCap: int | None = Field(None, ge=0, description="Hard cap limit")
    creditsSoftCap: int | None = Field(None, ge=0, description="Soft cap limit")
    creditsDailyLimit: int | None = Field(None, ge=0, description="Daily limit (for FREE tier)")


@router.post("/users/{user_id}/credits/adjust", response_model=UserDetailResponse)
async def adjust_user_credits(
    user_id: str,
    credit_data: CreditAdjustRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Adjust user credits (add or subtract).

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Calculate new credit usage
    current_credits = user.creditsUsed or 0
    new_credits = max(0, current_credits + credit_data.credits)  # Don't allow negative

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"creditsUsed": new_credits},
        include={"preferences": True},
    )

    logger.info(
        f"Admin {admin_user.email} adjusted credits for user {user_id}: "
        f"{current_credits} -> {new_credits} (reason: {credit_data.reason})"
    )

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "adjust_credits",
        "user",
        user_id,
        {
            "credits_adjusted": credit_data.credits,
            "old_credits": current_credits,
            "new_credits": new_credits,
            "reason": credit_data.reason,
        },
        db,
    )

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.post("/users/{user_id}/credits/reset", response_model=UserDetailResponse)
async def reset_user_credits(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Reset user credits to 0 for current period.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"creditsUsed": 0, "creditsUsedToday": 0},
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} reset credits for user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.put("/users/{user_id}/credits/limits", response_model=UserDetailResponse)
async def update_credit_limits(
    user_id: str,
    limit_data: CreditLimitUpdateRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Update custom credit limits for a user.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    update_dict: dict = {}
    if limit_data.creditsHardCap is not None:
        update_dict["creditsHardCap"] = limit_data.creditsHardCap
    if limit_data.creditsSoftCap is not None:
        update_dict["creditsSoftCap"] = limit_data.creditsSoftCap
    if limit_data.creditsDailyLimit is not None:
        update_dict["creditsDailyLimit"] = limit_data.creditsDailyLimit

    updated_user = await db.user.update(
        where={"id": user_id},
        data=update_dict,
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} updated credit limits for user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


# ============================================================================
# Subscription Management Endpoints
# ============================================================================


class SubscriptionSyncRequest(BaseModel):
    """Request model for syncing subscription from Stripe."""

    subscriptionId: str = Field(..., description="Stripe subscription ID")


@router.get("/subscriptions", response_model=dict)
async def list_subscriptions(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by subscription status"),
    tier: str | None = Query(None, description="Filter by tier"),
):
    """
    List all user subscriptions.

    Only accessible by admin users.
    """
    where: dict = {}
    if status:
        where["stripeSubscriptionStatus"] = status.upper()
    if tier:
        where["tier"] = tier.upper()

    # Count total
    total = await db.user.count(where=where)

    # Fetch paginated users
    skip = (page - 1) * pageSize
    users = await db.user.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"createdAt": "desc"},
        include={"preferences": True},
    )

    subscriptions = []
    for user in users:
        subscriptions.append(
            {
                "userId": user.id,
                "email": user.email,
                "name": user.name,
                "tier": str(user.tier),
                "stripeCustomerId": user.stripeCustomerId,
                "stripeSubscriptionId": user.stripeSubscriptionId,
                "stripeSubscriptionStatus": user.stripeSubscriptionStatus,
                "stripePriceId": user.stripePriceId,
                "subscriptionCurrentPeriodStart": (
                    user.subscriptionCurrentPeriodStart.isoformat()
                    if user.subscriptionCurrentPeriodStart
                    else None
                ),
                "subscriptionCurrentPeriodEnd": (
                    user.subscriptionCurrentPeriodEnd.isoformat()
                    if user.subscriptionCurrentPeriodEnd
                    else None
                ),
                "creditsUsed": user.creditsUsed or 0,
                "creditsHardCap": user.creditsHardCap,
                "createdAt": user.createdAt.isoformat(),
            }
        )

    total_pages = (total + pageSize - 1) // pageSize

    return {
        "subscriptions": subscriptions,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.post("/subscriptions/sync", response_model=dict)
async def sync_subscription(
    sync_data: SubscriptionSyncRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Manually sync subscription status from Stripe.

    Only accessible by admin users.
    """
    try:
        updated_user = await update_user_subscription_from_stripe(sync_data.subscriptionId, db)
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found in Stripe",
            )

        logger.info(
            f"Admin {admin_user.email} synced subscription {sync_data.subscriptionId} "
            f"for user {updated_user.id}"
        )

        return {
            "message": "Subscription synced successfully",
            "userId": updated_user.id,
            "email": updated_user.email,
            "tier": str(updated_user.tier),
            "status": updated_user.stripeSubscriptionStatus,
        }
    except Exception as e:
        logger.error(f"Error syncing subscription: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync subscription: {str(e)}",
        )


# ============================================================================
# Courses Management Endpoints
# ============================================================================


@router.get("/courses", response_model=dict)
async def list_all_courses(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    userId: str | None = Query(None, description="Filter by user ID"),
    difficulty: str | None = Query(None, description="Filter by difficulty"),
    isAIGenerated: bool | None = Query(None, description="Filter by AI-generated status"),
    archived: bool | None = Query(None, description="Filter by archived status"),
    search: str | None = Query(None, description="Search in title/description"),
):
    """
    List all courses across all users.

    Only accessible by admin users.
    """
    where: dict = {}
    if userId:
        where["userId"] = userId
    if difficulty:
        where["difficulty"] = difficulty.upper()
    if isAIGenerated is not None:
        where["isAIGenerated"] = isAIGenerated
    if archived is not None:
        where["archived"] = archived
    if search:
        where["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"description": {"contains": search, "mode": "insensitive"}},
        ]

    # Count total
    total = await db.course.count(where=where)

    # Fetch paginated courses
    skip = (page - 1) * pageSize
    courses = await db.course.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"createdAt": "desc"},
        include={"user": True, "modules": {"include": {"topics": True}}},
    )

    course_list = []
    for course in courses:
        total_topics = sum(len(module.topics) for module in course.modules)
        completed_topics = sum(
            1 for module in course.modules for topic in module.topics if topic.completed
        )
        progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0

        course_list.append(
            {
                "id": course.id,
                "userId": course.userId,
                "userEmail": course.user.email,
                "userName": course.user.name,
                "title": course.title,
                "description": course.description,
                "difficulty": str(course.difficulty),
                "isAIGenerated": course.isAIGenerated,
                "archived": course.archived,
                "progress": progress,
                "totalTopics": total_topics,
                "completedTopics": completed_topics,
                "moduleCount": len(course.modules),
                "createdAt": course.createdAt.isoformat(),
                "updatedAt": course.updatedAt.isoformat(),
            }
        )

    total_pages = (total + pageSize - 1) // pageSize

    return {
        "courses": course_list,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.get("/courses/{course_id}", response_model=dict)
async def get_course_details(
    course_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get detailed course information (admin view).

    Only accessible by admin users.
    """
    course = await db.course.find_unique(
        where={"id": course_id},
        include={
            "user": True,
            "modules": {
                "include": {"topics": {"include": {"note": True}}},
                "orderBy": {"order": "asc"},
            },
        },
    )

    if not course:
        raise ResourceNotFoundError("Course", course_id)

    total_topics = sum(len(module.topics) for module in course.modules)
    completed_topics = sum(
        1 for module in course.modules for topic in module.topics if topic.completed
    )
    progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0

    modules_data = []
    for module in course.modules:
        module_topics = len(module.topics)
        module_completed = sum(1 for topic in module.topics if topic.completed)
        module_progress = (module_completed / module_topics * 100) if module_topics > 0 else 0.0

        topics_data = []
        for topic in module.topics:
            topics_data.append(
                {
                    "id": topic.id,
                    "title": topic.title,
                    "content": topic.content,
                    "order": topic.order,
                    "completed": topic.completed,
                    "estimatedHours": topic.estimatedHours,
                    "createdAt": topic.createdAt.isoformat(),
                }
            )

        modules_data.append(
            {
                "id": module.id,
                "title": module.title,
                "description": module.description,
                "order": module.order,
                "completed": module.completed,
                "progress": module_progress,
                "totalTopics": module_topics,
                "completedTopics": module_completed,
                "topics": topics_data,
            }
        )

    return {
        "id": course.id,
        "userId": course.userId,
        "userEmail": course.user.email,
        "userName": course.user.name,
        "title": course.title,
        "description": course.description,
        "difficulty": str(course.difficulty),
        "targetDate": course.targetDate.isoformat() if course.targetDate else None,
        "isAIGenerated": course.isAIGenerated,
        "archived": course.archived,
        "progress": progress,
        "totalTopics": total_topics,
        "completedTopics": completed_topics,
        "modules": modules_data,
        "createdAt": course.createdAt.isoformat(),
        "updatedAt": course.updatedAt.isoformat(),
    }


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course_admin(
    course_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Delete a course (admin only).

    Only accessible by admin users.
    """
    course = await db.course.find_unique(where={"id": course_id})
    if not course:
        raise ResourceNotFoundError("Course", course_id)

    await db.course.delete(where={"id": course_id})

    logger.info(f"Admin {admin_user.email} deleted course {course_id}")

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "delete_course",
        "course",
        course_id,
        {"course_title": course.title if course else None},
        db,
    )

    return None


# ============================================================================
# Chat & AI Usage Monitoring Endpoints
# ============================================================================


@router.get("/chat/sessions", response_model=dict)
async def list_chat_sessions(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    userId: str | None = Query(
        None, description="Filter by user ID (deprecated, use search instead)"
    ),
    search: str | None = Query(None, description="Search by user ID, email, or name"),
):
    """
    List all chat sessions across users.

    Only accessible by admin users.
    """
    where: dict = {}

    # Support both userId (for backward compatibility) and search
    search_term = userId or search

    if search_term:
        # Find users matching search (by ID, email, or name)
        try:
            # First, try to find by exact ID match
            user_by_id = await db.user.find_unique(where={"id": search_term})

            if user_by_id:
                # Exact ID match found
                where["userId"] = user_by_id.id
            else:
                # Search by email or name (case-insensitive)
                matching_users = await db.user.find_many(
                    where={
                        "OR": [
                            {"email": {"contains": search_term, "mode": "insensitive"}},
                            {"name": {"contains": search_term, "mode": "insensitive"}},
                        ]
                    },
                    select={"id": True},
                )
                user_ids = [u.id for u in matching_users]
                if user_ids:
                    where["userId"] = {"in": user_ids}
                else:
                    # No matching users, return empty result immediately
                    return {
                        "sessions": [],
                        "total": 0,
                        "page": page,
                        "pageSize": pageSize,
                        "totalPages": 0,
                    }
        except Exception as e:
            logger.error(f"Error searching users: {e}", exc_info=True)
            # Return empty result on error
            return {
                "sessions": [],
                "total": 0,
                "page": page,
                "pageSize": pageSize,
                "totalPages": 0,
            }

    total = await db.chatsession.count(where=where)

    skip = (page - 1) * pageSize
    sessions = await db.chatsession.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"updatedAt": "desc"},
        include={"user": True, "messages": True},
    )

    session_list = []
    for session in sessions:
        total_tokens = sum(msg.tokenCount or 0 for msg in session.messages)
        message_count = len(session.messages)

        # Calculate costs and revenue for this session
        total_cost = sum(msg.costUsd or 0.0 for msg in session.messages)
        total_revenue = sum(msg.revenueUsd or 0.0 for msg in session.messages)

        session_list.append(
            {
                "id": session.id,
                "userId": session.userId,
                "userEmail": session.user.email,
                "userName": session.user.name,
                "title": session.title,
                "isActive": session.isActive,
                "messageCount": message_count,
                "totalTokens": total_tokens,
                "totalCostUsd": round(total_cost, 4),
                "totalRevenueUsd": round(total_revenue, 4),
                "profitUsd": round(total_revenue - total_cost, 4),
                "createdAt": session.createdAt.isoformat(),
                "updatedAt": session.updatedAt.isoformat(),
            }
        )

    total_pages = (total + pageSize - 1) // pageSize

    return {
        "sessions": session_list,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.get("/chat/stats", response_model=dict)
async def get_chat_statistics(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get platform-wide chat statistics.

    Only accessible by admin users.
    """
    # Get all chat sessions
    all_sessions = await db.chatsession.find_many(include={"messages": True})
    all_messages = await db.chatmessage.find_many()

    total_sessions = len(all_sessions)
    total_messages = len(all_messages)
    total_tokens = sum(msg.tokenCount or 0 for msg in all_messages)
    avg_tokens_per_message = total_tokens / total_messages if total_messages > 0 else 0.0

    # Calculate total costs and revenue
    total_cost_usd = sum(msg.costUsd or 0.0 for msg in all_messages)
    total_revenue_usd = sum(msg.revenueUsd or 0.0 for msg in all_messages)
    total_profit_usd = total_revenue_usd - total_cost_usd
    profit_margin = (total_profit_usd / total_revenue_usd * 100) if total_revenue_usd > 0 else 0.0

    # Get unique users who have chatted
    unique_users = len(set(msg.userId for msg in all_messages))

    # Get messages by date (last 30 days)
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)
    recent_messages = [msg for msg in all_messages if msg.createdAt >= thirty_days_ago]

    daily_stats = {}
    for msg in recent_messages:
        date_str = msg.createdAt.date().isoformat()
        if date_str not in daily_stats:
            daily_stats[date_str] = {"messages": 0, "tokens": 0, "costUsd": 0.0, "revenueUsd": 0.0}
        daily_stats[date_str]["messages"] += 1
        daily_stats[date_str]["tokens"] += msg.tokenCount or 0
        daily_stats[date_str]["costUsd"] += msg.costUsd or 0.0
        daily_stats[date_str]["revenueUsd"] += msg.revenueUsd or 0.0

    return {
        "totalSessions": total_sessions,
        "totalMessages": total_messages,
        "totalTokens": total_tokens,
        "averageTokensPerMessage": round(avg_tokens_per_message, 2),
        "uniqueUsers": unique_users,
        "totalCostUsd": round(total_cost_usd, 4),
        "totalRevenueUsd": round(total_revenue_usd, 4),
        "totalProfitUsd": round(total_profit_usd, 4),
        "profitMargin": round(profit_margin, 2),
        "dailyStats": daily_stats,
    }


# ============================================================================
# Referral Management Endpoints
# ============================================================================


@router.get("/referrals", response_model=dict)
async def list_referrals(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    referrerId: str | None = Query(None, description="Filter by referrer ID"),
    isClaimed: bool | None = Query(None, description="Filter by claimed status"),
):
    """
    List all referral rewards.

    Only accessible by admin users.
    """
    where: dict = {}
    if referrerId:
        where["referrerId"] = referrerId
    if isClaimed is not None:
        where["isClaimed"] = isClaimed

    total = await db.referralreward.count(where=where)

    skip = (page - 1) * pageSize
    rewards = await db.referralreward.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"createdAt": "desc"},
        include={"referrer": True, "referredUser": True},
    )

    reward_list = []
    for reward in rewards:
        reward_list.append(
            {
                "id": reward.id,
                "referrerId": reward.referrerId,
                "referrerEmail": reward.referrer.email,
                "referrerName": reward.referrer.name,
                "referredUserId": reward.referredUserId,
                "referredUserEmail": reward.referredUser.email,
                "referredUserName": reward.referredUser.name,
                "rewardType": reward.rewardType,
                "tokens": reward.tokens,
                "isClaimed": reward.isClaimed,
                "claimedAt": reward.claimedAt.isoformat() if reward.claimedAt else None,
                "createdAt": reward.createdAt.isoformat(),
            }
        )

    total_pages = (total + pageSize - 1) // pageSize

    return {
        "rewards": reward_list,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.get("/referrals/stats", response_model=dict)
async def get_referral_statistics(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get platform-wide referral statistics.

    Only accessible by admin users.
    """
    all_rewards = await db.referralreward.find_many(
        include={"referrer": True, "referredUser": True}
    )

    total_rewards = len(all_rewards)
    claimed_rewards = sum(1 for r in all_rewards if r.isClaimed)
    total_tokens_awarded = sum(r.tokens for r in all_rewards)
    total_tokens_claimed = sum(r.tokens for r in all_rewards if r.isClaimed)

    # Get top referrers
    referrer_stats = {}
    for reward in all_rewards:
        referrer_id = reward.referrerId
        if referrer_id not in referrer_stats:
            referrer_stats[referrer_id] = {
                "email": reward.referrer.email,
                "name": reward.referrer.name,
                "totalReferrals": 0,
                "totalTokens": 0,
            }
        referrer_stats[referrer_id]["totalReferrals"] += 1
        referrer_stats[referrer_id]["totalTokens"] += reward.tokens

    top_referrers = sorted(
        referrer_stats.values(), key=lambda x: x["totalReferrals"], reverse=True
    )[:10]

    # Get rewards by type
    signup_rewards = sum(1 for r in all_rewards if r.rewardType == "signup")
    subscription_rewards = sum(1 for r in all_rewards if r.rewardType == "subscription")

    return {
        "totalRewards": total_rewards,
        "claimedRewards": claimed_rewards,
        "unclaimedRewards": total_rewards - claimed_rewards,
        "totalTokensAwarded": total_tokens_awarded,
        "totalTokensClaimed": total_tokens_claimed,
        "topReferrers": top_referrers,
        "signupRewards": signup_rewards,
        "subscriptionRewards": subscription_rewards,
    }


# ============================================================================
# Advanced Analytics Endpoints
# ============================================================================


@router.get("/analytics/revenue", response_model=dict)
async def get_revenue_analytics(
    admin_user: AdminUser,
    db: DBDep,
    startDate: str | None = Query(None, description="Start date (ISO format)"),
    endDate: str | None = Query(None, description="End date (ISO format)"),
):
    """
    Get revenue analytics (MRR, ARR, churn, etc.).

    Only accessible by admin users.
    """
    # Get all users with subscriptions
    users = await db.user.find_many(
        where={"stripeSubscriptionStatus": {"not": None}},
        include={"preferences": True},
    )

    # Calculate MRR (Monthly Recurring Revenue)
    mrr = 0.0
    monthly_subscriptions = 0
    yearly_subscriptions = 0

    # Note: This is a simplified calculation. In production, you'd fetch actual prices from Stripe
    # For now, we'll estimate based on tier
    for user in users:
        if user.stripeSubscriptionStatus == "active":
            tier = str(user.tier)
            if tier == "PREMIUM_MONTHLY":
                mrr += 9.99  # Example monthly price
                monthly_subscriptions += 1
            elif tier == "PREMIUM_YEARLY":
                mrr += 99.99 / 12  # Yearly price / 12 months
                yearly_subscriptions += 1

    arr = mrr * 12  # Annual Recurring Revenue

    # Calculate churn (simplified - users with canceled subscriptions)
    canceled_users = await db.user.count(where={"stripeSubscriptionStatus": "canceled"})
    total_active = len([u for u in users if u.stripeSubscriptionStatus == "active"])
    churn_rate = (
        (canceled_users / (total_active + canceled_users) * 100)
        if (total_active + canceled_users) > 0
        else 0.0
    )

    # Get subscription growth (new subscriptions in last 30 days)
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)
    recent_subscriptions = await db.user.count(
        where={
            "stripeSubscriptionStatus": "active",
            "subscriptionCurrentPeriodStart": {"gte": thirty_days_ago},
        }
    )

    return {
        "mrr": round(mrr, 2),
        "arr": round(arr, 2),
        "monthlySubscriptions": monthly_subscriptions,
        "yearlySubscriptions": yearly_subscriptions,
        "totalActiveSubscriptions": total_active,
        "churnRate": round(churn_rate, 2),
        "newSubscriptionsLast30Days": recent_subscriptions,
    }


@router.get("/analytics/retention", response_model=dict)
async def get_retention_analytics(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get user retention metrics (DAU, MAU, retention cohorts).

    Only accessible by admin users.
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    # Daily Active Users (DAU) - users who had activity today
    dau = await db.user.count(
        where={
            "isActive": True,
            "updatedAt": {"gte": today_start},
        }
    )

    # Monthly Active Users (MAU) - users active in last 30 days
    mau = await db.user.count(
        where={
            "isActive": True,
            "updatedAt": {"gte": thirty_days_ago},
        }
    )

    # Calculate retention cohorts (simplified)
    # Get users who signed up in each month
    all_users = await db.user.find_many(
        where={"role": "USER", "isActive": True}, order={"createdAt": "asc"}
    )

    cohorts = {}
    for user in all_users:
        signup_month = user.createdAt.strftime("%Y-%m")
        if signup_month not in cohorts:
            cohorts[signup_month] = {"signups": 0, "active": 0}
        cohorts[signup_month]["signups"] += 1

        # Check if user was active in last 30 days
        if user.updatedAt >= thirty_days_ago:
            cohorts[signup_month]["active"] += 1

    # Calculate retention rates
    cohort_retention = {}
    for month, data in cohorts.items():
        retention_rate = (data["active"] / data["signups"] * 100) if data["signups"] > 0 else 0.0
        cohort_retention[month] = {
            "signups": data["signups"],
            "active": data["active"],
            "retentionRate": round(retention_rate, 2),
        }

    return {
        "dau": dau,
        "mau": mau,
        "dauMauRatio": round((dau / mau * 100) if mau > 0 else 0.0, 2),
        "cohortRetention": cohort_retention,
    }


@router.get("/analytics/growth", response_model=dict)
async def get_growth_analytics(
    admin_user: AdminUser,
    db: DBDep,
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
):
    """
    Get growth metrics (signups, conversions, referrals).

    Only accessible by admin users.
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # Get signups in period
    signups = await db.user.count(where={"createdAt": {"gte": start_date}, "role": "USER"})

    # Get conversions (FREE to Premium)
    conversions = await db.user.count(
        where={
            "createdAt": {"gte": start_date},
            "tier": {"in": ["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]},
        }
    )

    # Get referrals in period
    referrals = await db.referralreward.count(where={"createdAt": {"gte": start_date}})

    # Get daily breakdown
    daily_stats = {}
    users_in_period = await db.user.find_many(
        where={"createdAt": {"gte": start_date}, "role": "USER"}
    )
    for user in users_in_period:
        date_str = user.createdAt.date().isoformat()
        if date_str not in daily_stats:
            daily_stats[date_str] = {"signups": 0, "conversions": 0}
        daily_stats[date_str]["signups"] += 1
        if str(user.tier) in ["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]:
            daily_stats[date_str]["conversions"] += 1

    return {
        "periodDays": days,
        "totalSignups": signups,
        "totalConversions": conversions,
        "conversionRate": round((conversions / signups * 100) if signups > 0 else 0.0, 2),
        "totalReferrals": referrals,
        "dailyStats": daily_stats,
    }


# ============================================================================
# User Impersonation Endpoints
# ============================================================================


@router.post("/users/{user_id}/impersonate", response_model=dict)
async def impersonate_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Generate an impersonation token for a user (admin only).

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent impersonating other admins
    if user.role == "ADMIN" and user.id != admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot impersonate other admin users",
        )

    # Generate impersonation token (expires in 1 hour)
    from ..core.security import create_access_token

    impersonation_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": user.id,
            "impersonated_by": admin_user.id,
            "is_impersonation": True,
        },
        expires_delta=timedelta(hours=1),
    )

    logger.info(f"Admin {admin_user.email} generated impersonation token for user {user_id}")

    # Log audit trail
    await log_admin_action(
        admin_user.id,
        "impersonate_user",
        "user",
        user_id,
        {"impersonated_user_email": user.email},
        db,
    )

    return {
        "impersonationToken": impersonation_token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        },
        "expiresIn": 3600,  # 1 hour in seconds
    }


# ============================================================================
# System Configuration Endpoints
# ============================================================================


class SystemConfigUpdateRequest(BaseModel):
    """Request model for updating system configuration."""

    creditLimits: dict[str, dict[str, int]] | None = Field(
        None, description="Credit limits per tier"
    )
    maintenanceMode: bool | None = Field(None, description="Enable/disable maintenance mode")
    featureFlags: dict[str, bool] | None = Field(None, description="Feature flags")


@router.get("/config", response_model=dict)
async def get_system_config(
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get current system configuration.

    Only accessible by admin users.
    """
    from ..services.credit_service import CREDIT_LIMITS

    # In production, you'd store config in database or environment variables
    # For now, we'll return the current credit limits from the service
    return {
        "creditLimits": CREDIT_LIMITS,
        "maintenanceMode": False,  # Would come from database/env in production
        "featureFlags": {},  # Would come from database/env in production
    }


@router.put("/config", response_model=dict)
async def update_system_config(
    config_data: SystemConfigUpdateRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Update system configuration.

    Only accessible by admin users.
    Note: In production, this would update database or environment variables.
    For now, this is a placeholder that logs the action.
    """
    # Log the configuration change
    await log_admin_action(
        admin_user.id,
        "update_system_config",
        "system",
        None,
        {
            "credit_limits_updated": config_data.creditLimits is not None,
            "maintenance_mode_updated": config_data.maintenanceMode is not None,
            "feature_flags_updated": config_data.featureFlags is not None,
        },
        db,
    )

    logger.info(
        f"Admin {admin_user.email} updated system configuration. "
        f"Note: In production, this would persist changes to database/env."
    )

    return {
        "message": "Configuration update logged. In production, changes would be persisted.",
        "updated": {
            "creditLimits": config_data.creditLimits is not None,
            "maintenanceMode": config_data.maintenanceMode is not None,
            "featureFlags": config_data.featureFlags is not None,
        },
    }
