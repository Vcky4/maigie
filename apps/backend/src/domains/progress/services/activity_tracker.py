"""
Activity Tracker Service.

Handles:
- Updating lastSeenAt on the User model when meaningful activity occurs
- Updating UserStreak (consecutive study days)

Meaningful activity includes:
- Sending a chat message
- Starting a study session
- Creating/completing a course
- Any AI action (goal creation, schedule creation, etc.)

This does NOT include passive actions like login, viewing pages, or token refresh.
"""

import logging
from datetime import UTC, datetime, timedelta

from src.core.database import db

logger = logging.getLogger(__name__)


async def record_activity(user_id: str) -> None:
    """
    Record meaningful study activity (chat message, study session).

    Only updates the streak. lastSeenAt is handled separately by the auth
    dependency on every authenticated request.
    """
    today = datetime.now(UTC).date()

    # Update streak
    try:
        await _update_streak(user_id, today)
    except Exception as e:
        logger.warning("Failed to update streak for user %s: %s", user_id, e)


async def _update_streak(user_id: str, today) -> None:
    """
    Update the user's study streak.

    Rules:
    - If lastStudyDate is today: do nothing (already counted today)
    - If lastStudyDate is yesterday: increment streak (consecutive day)
    - If lastStudyDate is older: reset streak to 1 (streak broken)
    - If no streak record exists: create one with streak = 1
    """
    streak = await db.userstreak.find_unique(where={"userId": user_id})

    today_dt = datetime(today.year, today.month, today.day, tzinfo=UTC)
    yesterday = today - timedelta(days=1)

    if streak is None:
        # First time: create streak record
        await db.userstreak.create(
            data={
                "userId": user_id,
                "currentStreak": 1,
                "longestStreak": 1,
                "lastStudyDate": today_dt,
            }
        )
        return

    last_study = streak.lastStudyDate.date() if streak.lastStudyDate else None

    if last_study == today:
        # Already studied today, nothing to do
        return

    if last_study == yesterday:
        # Consecutive day: increment streak
        new_streak = streak.currentStreak + 1
        new_longest = max(streak.longestStreak, new_streak)
        await db.userstreak.update(
            where={"userId": user_id},
            data={
                "currentStreak": new_streak,
                "longestStreak": new_longest,
                "lastStudyDate": today_dt,
            },
        )
    else:
        # Streak broken (missed a day or more): reset to 1
        await db.userstreak.update(
            where={"userId": user_id},
            data={
                "currentStreak": 1,
                "longestStreak": max(streak.longestStreak, 1),
                "lastStudyDate": today_dt,
            },
        )
