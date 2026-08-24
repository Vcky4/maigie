"""
Activity Tracker Service.

Handles:
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

from ..repository import progress_repo

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
        new_streak = await _update_streak(user_id, today)
    except Exception as e:
        logger.warning("Failed to update streak for user %s: %s", user_id, e)
        return

    if new_streak is None:
        # The streak did not move — the learner had already been counted today.
        return

    # Announce it, so a milestone can be celebrated.
    #
    # `personal_learning.handle_streak_updated` has been waiting for this event since it was written:
    # it turns 7, 14, 30, 60, 100 and 365 days into a celebration notification. **Nothing has ever
    # emitted `progress.streak_updated`** — the name existed only as an unused constant in
    # `shared/events/types.py` — so no learner has ever been told they hit a streak milestone.
    #
    # Emitted outside the `try` above on purpose: a dispatch failure is not a failure to update the
    # streak, and logging it as one would send the next reader to the wrong place. The bus isolates
    # handler failures itself.
    from src.shared.events import emit

    await emit("progress.streak_updated", {"user_id": user_id, "streak_count": new_streak})


async def _update_streak(user_id: str, today) -> int | None:
    """
    Update the user's study streak.

    Rules:
    - If lastStudyDate is today: do nothing (already counted today)
    - If lastStudyDate is yesterday: increment streak (consecutive day)
    - If lastStudyDate is older: reset streak to 1 (streak broken)
    - If no streak record exists: create one with streak = 1

    Returns the learner's new current streak, or `None` when nothing changed because today was already
    counted. The caller announces the change, and "nothing changed" must not be announced — a learner
    studying twice in one day is not a new streak day and should not be congratulated twice.
    """
    streak = await progress_repo.get_streak(user_id)

    today_dt = datetime(today.year, today.month, today.day, tzinfo=UTC)
    yesterday = today - timedelta(days=1)

    if streak is None:
        # First time: create streak record
        await progress_repo.upsert_streak(
            user_id,
            {
                "currentStreak": 1,
                "longestStreak": 1,
                "lastStudyDate": today_dt,
            },
        )
        return 1

    last_study = streak.last_study_date.date() if streak.last_study_date else None

    if last_study == today:
        # Already studied today, nothing to do
        return None

    if last_study == yesterday:
        # Consecutive day: increment streak
        new_streak = streak.current_streak + 1
        new_longest = max(streak.longest_streak, new_streak)
        await progress_repo.upsert_streak(
            user_id,
            {
                "currentStreak": new_streak,
                "longestStreak": new_longest,
                "lastStudyDate": today_dt,
            },
        )
        return new_streak

    # Streak broken (missed a day or more): reset to 1
    await progress_repo.upsert_streak(
        user_id,
        {
            "currentStreak": 1,
            "longestStreak": max(streak.longest_streak, 1),
            "lastStudyDate": today_dt,
        },
    )
    return 1
