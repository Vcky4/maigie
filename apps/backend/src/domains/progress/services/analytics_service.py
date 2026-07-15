"""
Analytics — study sessions, streaks, achievements, and reporting.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.events import emit

from ..repository import progress_repo

logger = logging.getLogger(__name__)


async def start_study_session(*, user_id: str, course_id: str | None = None, topic_id: str | None = None) -> dict[str, Any]:
    """Start a study session (or return existing active one)."""
    active = await progress_repo.find_active_session(user_id)
    if active:
        return {"sessionId": active.id, "startTime": active.startTime.isoformat(), "message": "Active session exists"}

    session = await progress_repo.create_session({
        "userId": user_id,
        "startTime": datetime.now(UTC),
        "duration": 0.0,
        "courseId": course_id,
        "topicId": topic_id,
    })

    # Track activity for streak
    try:
        from src.services.activity_tracker import record_activity
        await record_activity(user_id)
    except Exception:
        pass

    return {"sessionId": session.id, "startTime": session.startTime.isoformat()}


async def stop_study_session(*, session_id: str, user_id: str) -> dict[str, Any]:
    """Stop a study session and update streak."""
    from src.shared.database import db

    session = await db.studysession.find_unique(where={"id": session_id})
    if not session or session.userId != user_id:
        from src.shared.exceptions import NotFoundError
        raise NotFoundError("StudySession", session_id)

    if session.endTime:
        return {"sessionId": session.id, "duration": session.duration, "message": "Already ended"}

    end_time = datetime.now(UTC)
    start_time = session.startTime
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    duration_minutes = (end_time - start_time).total_seconds() / 60

    await progress_repo.update_session(session_id, {"endTime": end_time, "duration": duration_minutes})

    # Update streak
    await _update_streak(user_id, end_time)

    await emit("progress.study_session_completed", {
        "user_id": user_id, "session_id": session_id, "duration_minutes": duration_minutes
    })

    return {"sessionId": session_id, "duration": duration_minutes, "endTime": end_time.isoformat()}


async def get_streak(*, user_id: str) -> dict[str, Any]:
    """Get current streak info."""
    streak = await progress_repo.get_streak(user_id)
    if not streak:
        return {"currentStreak": 0, "longestStreak": 0, "lastStudyDate": None}
    return {
        "currentStreak": streak.currentStreak or 0,
        "longestStreak": streak.longestStreak or 0,
        "lastStudyDate": streak.lastStudyDate.isoformat() if streak.lastStudyDate else None,
    }


async def list_achievements(*, user_id: str) -> list[dict[str, Any]]:
    """Get all unlocked achievements."""
    achievements = await progress_repo.list_achievements(user_id)
    return [
        {
            "id": a.id,
            "type": str(a.achievementType),
            "title": a.title,
            "description": a.description or "",
            "icon": a.icon,
            "unlockedAt": a.unlockedAt.isoformat(),
            "metadata": a.metadata,
        }
        for a in achievements
    ]


async def _update_streak(user_id: str, study_datetime: datetime) -> None:
    """Update user's study streak."""
    study_date = study_datetime.date()
    streak = await progress_repo.get_streak(user_id)

    if not streak:
        await progress_repo.upsert_streak(user_id, {
            "currentStreak": 1, "longestStreak": 1, "lastStudyDate": study_datetime
        })
        return

    if streak.lastStudyDate:
        last_date = streak.lastStudyDate.date() if isinstance(streak.lastStudyDate, datetime) else streak.lastStudyDate
        days_diff = (study_date - last_date).days
        if days_diff == 0:
            return
        elif days_diff == 1:
            new_streak = streak.currentStreak + 1
        else:
            new_streak = 1
    else:
        new_streak = 1

    longest = max(streak.longestStreak or 0, new_streak)
    await progress_repo.upsert_streak(user_id, {
        "currentStreak": new_streak, "longestStreak": longest, "lastStudyDate": study_datetime
    })
