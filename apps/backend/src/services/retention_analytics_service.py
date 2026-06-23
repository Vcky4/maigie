"""
Retention Analytics Service.

Computes real retention cohort data, engagement scores, feature adoption rates,
time-to-first-value, and at-risk user identification.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.database import db

logger = logging.getLogger(__name__)


async def compute_retention_cohorts(months: int = 6) -> dict[str, Any]:
    """
    Compute monthly signup cohorts with Day-1, Day-7, Day-30 retention rates.

    For each signup cohort (month), calculates what % of users had activity
    within 1 day, 7 days, and 30 days of signup.

    Activity is determined by chat messages (most reliable signal).
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=months * 30)

    # Get all users who signed up in the window
    users = await db.user.find_many(
        where={
            "role": "USER",
            "createdAt": {"gte": start_date},
        },
        order={"createdAt": "asc"},
    )

    if not users:
        return {}

    # Get all chat messages for these users (activity signal)
    user_ids = [u.id for u in users]
    messages = await db.chatmessage.find_many(
        where={
            "userId": {"in": user_ids},
            "role": "USER",
        },
        order={"createdAt": "asc"},
    )

    # Build a map of userId -> list of activity timestamps
    user_activity: dict[str, list[datetime]] = defaultdict(list)
    for msg in messages:
        if msg.userId:
            user_activity[msg.userId].append(msg.createdAt)

    # Group users by signup month
    cohorts: dict[str, dict[str, Any]] = {}

    for user in users:
        month_key = user.createdAt.strftime("%Y-%m")
        if month_key not in cohorts:
            cohorts[month_key] = {
                "signups": 0,
                "day1_retained": 0,
                "day7_retained": 0,
                "day30_retained": 0,
                "currently_active": 0,
            }

        cohorts[month_key]["signups"] += 1
        activities = user_activity.get(user.id, [])

        if not activities:
            continue

        signup_date = user.createdAt

        # Day 1: Any activity between 1h and 48h after signup
        day1_start = signup_date + timedelta(hours=1)
        day1_end = signup_date + timedelta(hours=48)
        if any(day1_start <= a <= day1_end for a in activities):
            cohorts[month_key]["day1_retained"] += 1

        # Day 7: Any activity between day 2 and day 10
        day7_start = signup_date + timedelta(days=2)
        day7_end = signup_date + timedelta(days=10)
        if any(day7_start <= a <= day7_end for a in activities):
            cohorts[month_key]["day7_retained"] += 1

        # Day 30: Any activity between day 14 and day 45
        day30_start = signup_date + timedelta(days=14)
        day30_end = signup_date + timedelta(days=45)
        if any(day30_start <= a <= day30_end for a in activities):
            cohorts[month_key]["day30_retained"] += 1

        # Currently active: activity in last 7 days
        seven_days_ago = now - timedelta(days=7)
        if any(a >= seven_days_ago for a in activities):
            cohorts[month_key]["currently_active"] += 1

    # Calculate rates
    result = {}
    for month, data in cohorts.items():
        signups = data["signups"]
        result[month] = {
            "signups": signups,
            "day1Retained": data["day1_retained"],
            "day1Rate": round(data["day1_retained"] / signups * 100, 1) if signups > 0 else 0,
            "day7Retained": data["day7_retained"],
            "day7Rate": round(data["day7_retained"] / signups * 100, 1) if signups > 0 else 0,
            "day30Retained": data["day30_retained"],
            "day30Rate": round(data["day30_retained"] / signups * 100, 1) if signups > 0 else 0,
            "currentlyActive": data["currently_active"],
            "currentRetentionRate": (
                round(data["currently_active"] / signups * 100, 1) if signups > 0 else 0
            ),
        }

    return result


async def compute_feature_adoption() -> dict[str, Any]:
    """
    Compute feature adoption rates across the platform.

    Measures what % of active users have used each major feature.
    """
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)

    # Active users = users who sent at least 1 message in last 30 days
    active_user_ids_raw = await db.chatmessage.find_many(
        where={
            "role": "USER",
            "createdAt": {"gte": thirty_days_ago},
        },
        distinct=["userId"],
    )
    active_user_ids = list({m.userId for m in active_user_ids_raw if m.userId})
    total_active = len(active_user_ids)

    if total_active == 0:
        return {
            "totalActiveUsers": 0,
            "features": {},
        }

    # Feature: Created a course
    course_users = await db.course.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    course_user_count = len({c.userId for c in course_users})

    # Feature: Set a goal
    goal_users = await db.goal.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    goal_user_count = len({g.userId for g in goal_users})

    # Feature: Used schedule
    schedule_users = await db.scheduleblock.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    schedule_user_count = len({s.userId for s in schedule_users})

    # Feature: Used spaced repetition (reviews)
    review_users = await db.reviewitem.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    review_user_count = len({r.userId for r in review_users})

    # Feature: Created notes
    note_users = await db.note.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    note_user_count = len({n.userId for n in note_users})

    # Feature: Joined a circle
    circle_members = await db.circlemember.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    circle_user_count = len({cm.userId for cm in circle_members})

    # Feature: Used exam prep
    exam_prep_users = await db.examprep.find_many(
        where={"userId": {"in": active_user_ids}},
        distinct=["userId"],
    )
    exam_user_count = len({e.userId for e in exam_prep_users})

    features = {
        "courses": {
            "users": course_user_count,
            "rate": round(course_user_count / total_active * 100, 1),
        },
        "goals": {
            "users": goal_user_count,
            "rate": round(goal_user_count / total_active * 100, 1),
        },
        "schedule": {
            "users": schedule_user_count,
            "rate": round(schedule_user_count / total_active * 100, 1),
        },
        "reviews": {
            "users": review_user_count,
            "rate": round(review_user_count / total_active * 100, 1),
        },
        "notes": {
            "users": note_user_count,
            "rate": round(note_user_count / total_active * 100, 1),
        },
        "circles": {
            "users": circle_user_count,
            "rate": round(circle_user_count / total_active * 100, 1),
        },
        "examPrep": {
            "users": exam_user_count,
            "rate": round(exam_user_count / total_active * 100, 1),
        },
    }

    return {
        "totalActiveUsers": total_active,
        "features": features,
    }


async def compute_time_to_first_value() -> dict[str, Any]:
    """
    Compute average time from signup to first meaningful action.

    First value = first course created OR first goal set OR first chat message.
    """
    now = datetime.now(UTC)
    ninety_days_ago = now - timedelta(days=90)

    # Get recent signups
    users = await db.user.find_many(
        where={
            "role": "USER",
            "createdAt": {"gte": ninety_days_ago},
        },
        take=500,
    )

    if not users:
        return {"averageHours": 0, "medianHours": 0, "sampleSize": 0}

    user_ids = [u.id for u in users]

    # Get first course creation time per user
    courses = await db.course.find_many(
        where={"userId": {"in": user_ids}},
        order={"createdAt": "asc"},
    )
    first_course: dict[str, datetime] = {}
    for c in courses:
        if c.userId not in first_course:
            first_course[c.userId] = c.createdAt

    # Get first chat message per user
    messages = await db.chatmessage.find_many(
        where={"userId": {"in": user_ids}, "role": "USER"},
        order={"createdAt": "asc"},
    )
    first_message: dict[str, datetime] = {}
    for m in messages:
        if m.userId and m.userId not in first_message:
            first_message[m.userId] = m.createdAt

    # Calculate time-to-first-value for each user
    ttfv_hours: list[float] = []
    for user in users:
        first_actions = []
        if user.id in first_course:
            first_actions.append(first_course[user.id])
        if user.id in first_message:
            first_actions.append(first_message[user.id])

        if first_actions:
            earliest = min(first_actions)
            hours_diff = (earliest - user.createdAt).total_seconds() / 3600
            # Only count reasonable values (within 30 days)
            if 0 < hours_diff < 720:
                ttfv_hours.append(hours_diff)

    if not ttfv_hours:
        return {"averageHours": 0, "medianHours": 0, "sampleSize": 0}

    ttfv_hours.sort()
    avg_hours = sum(ttfv_hours) / len(ttfv_hours)
    median_hours = ttfv_hours[len(ttfv_hours) // 2]

    return {
        "averageHours": round(avg_hours, 1),
        "medianHours": round(median_hours, 1),
        "sampleSize": len(ttfv_hours),
    }


async def compute_nudge_effectiveness() -> dict[str, Any]:
    """
    Compute how effective nudges are at re-engaging users.

    Measures: of all nudges sent, what % led to user activity within 24h.
    """
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)

    # Get nudges sent in last 30 days
    nudges = await db.aiagenttask.find_many(
        where={
            "status": {"in": ["sent", "acted_on"]},
            "createdAt": {"gte": thirty_days_ago},
        },
    )

    if not nudges:
        return {
            "totalNudges": 0,
            "actedOn": 0,
            "effectivenessRate": 0,
            "byType": {},
        }

    # Count by type
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"sent": 0, "actedOn": 0})
    total_acted = 0

    for nudge in nudges:
        by_type[nudge.taskType]["sent"] += 1
        if nudge.status == "acted_on":
            by_type[nudge.taskType]["actedOn"] += 1
            total_acted += 1

    type_rates = {}
    for task_type, counts in by_type.items():
        rate = round(counts["actedOn"] / counts["sent"] * 100, 1) if counts["sent"] > 0 else 0
        type_rates[task_type] = {
            "sent": counts["sent"],
            "actedOn": counts["actedOn"],
            "rate": rate,
        }

    return {
        "totalNudges": len(nudges),
        "actedOn": total_acted,
        "effectivenessRate": round(total_acted / len(nudges) * 100, 1) if nudges else 0,
        "byType": type_rates,
    }


async def get_users_at_risk(limit: int = 50) -> list[dict[str, Any]]:
    """
    Identify users at risk of churning.

    Criteria:
    - Were active (had activity) but haven't been seen in 3-14 days
    - Prioritized by: subscription tier (premium first), streak length, recency
    """
    now = datetime.now(UTC)
    three_days_ago = now - timedelta(days=3)
    fourteen_days_ago = now - timedelta(days=14)

    # Find users who were active but dropped off
    # "Active" = sent at least one chat message ever
    # "At risk" = no meaningful activity in last 3-14 days
    at_risk_users = await db.user.find_many(
        where={
            "role": "USER",
            "isActive": True,
            "isOnboarded": True,
            "OR": [
                {"lastSeenAt": {"lt": three_days_ago, "gt": fourteen_days_ago}},
                {"lastSeenAt": None, "updatedAt": {"lt": three_days_ago, "gt": fourteen_days_ago}},
            ],
            # Must have at least some history
            "chatMessages": {"some": {}},
        },
        include={
            "userStreak": True,
        },
        order={"updatedAt": "desc"},
        take=limit * 2,  # Get extra to filter/sort
    )

    # Enrich with activity data
    results = []
    for user in at_risk_users:
        last_active = user.lastSeenAt or user.updatedAt
        days_inactive = (now - last_active).days
        streak = user.userStreak

        results.append(
            {
                "userId": user.id,
                "email": user.email,
                "name": user.name,
                "tier": str(user.tier),
                "daysInactive": days_inactive,
                "currentStreak": streak.currentStreak if streak else 0,
                "longestStreak": streak.longestStreak if streak else 0,
                "lastActivity": last_active.isoformat(),
                "signupDate": user.createdAt.isoformat(),
                "riskLevel": _calculate_risk_level(days_inactive, str(user.tier), streak),
            }
        )

    # Sort by risk level (high first), then by tier (premium first)
    tier_priority = {"PREMIUM_YEARLY": 0, "PREMIUM_MONTHLY": 1, "FREE": 2}
    risk_priority = {"high": 0, "medium": 1, "low": 2}

    results.sort(
        key=lambda u: (
            risk_priority.get(u["riskLevel"], 3),
            tier_priority.get(u["tier"], 3),
            -u["daysInactive"],
        )
    )

    return results[:limit]


def _calculate_risk_level(days_inactive: int, tier: str, streak) -> str:
    """Calculate churn risk level for a user."""
    # Premium users at risk = always high priority
    if tier in ("PREMIUM_MONTHLY", "PREMIUM_YEARLY"):
        if days_inactive >= 5:
            return "high"
        return "medium"

    # Free users with streaks about to break
    if streak and streak.currentStreak >= 7 and days_inactive >= 3:
        return "high"

    if days_inactive >= 10:
        return "high"
    elif days_inactive >= 5:
        return "medium"
    return "low"


async def compute_weekly_retention_summary() -> dict[str, Any]:
    """
    Compute a quick weekly retention summary for the dashboard.

    Returns key numbers at a glance.
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    thirty_days_ago = now - timedelta(days=30)

    # DAU (Daily Active Users) - users seen today (any authenticated request)
    dau = await db.user.count(
        where={
            "role": "USER",
            "lastSeenAt": {"gte": today_start},
        }
    )

    # WAU (Weekly Active Users) - users seen in last 7 days
    wau = await db.user.count(
        where={
            "role": "USER",
            "lastSeenAt": {"gte": seven_days_ago},
        }
    )

    # Previous week WAU for comparison
    prev_wau = await db.user.count(
        where={
            "role": "USER",
            "lastSeenAt": {"gte": fourteen_days_ago, "lt": seven_days_ago},
        }
    )

    # MAU (Monthly Active Users) - users seen in last 30 days
    mau = await db.user.count(
        where={
            "role": "USER",
            "lastSeenAt": {"gte": thirty_days_ago},
        }
    )

    # Users at risk count
    three_days_ago = now - timedelta(days=3)
    at_risk_count = await db.user.count(
        where={
            "role": "USER",
            "isActive": True,
            "isOnboarded": True,
            "OR": [
                {"lastSeenAt": {"lt": three_days_ago, "gt": fourteen_days_ago}},
                {"lastSeenAt": None, "updatedAt": {"lt": three_days_ago, "gt": fourteen_days_ago}},
            ],
            "chatMessages": {"some": {}},
        }
    )

    # WAU change
    wau_change = round(((wau - prev_wau) / prev_wau * 100) if prev_wau > 0 else 0, 1)

    return {
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "wauChange": wau_change,
        "dauMauRatio": round((dau / mau * 100) if mau > 0 else 0, 1),
        "atRiskCount": at_risk_count,
    }
