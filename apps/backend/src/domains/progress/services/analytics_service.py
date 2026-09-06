"""
Analytics — study sessions, streaks, achievements, and reporting.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.events import ProgressEvents, emit

from ..repository import progress_repo

logger = logging.getLogger(__name__)


async def start_study_session(
    *, user_id: str, course_id: str | None = None, topic_id: str | None = None
) -> dict[str, Any]:
    """Start a study session (or return existing active one)."""
    active = await progress_repo.find_active_session(user_id)
    if active:
        return {
            "sessionId": active.id,
            "startTime": active.start_time.isoformat(),
            "message": "Active session exists",
        }

    session = await progress_repo.create_session(
        {
            "userId": user_id,
            "startTime": datetime.now(UTC),
            "duration": 0.0,
            "courseId": course_id,
            "topicId": topic_id,
        }
    )

    # Track activity for streak
    try:
        from src.domains.progress.services.activity_tracker import record_activity

        await record_activity(user_id)
    except Exception:
        pass

    return {"sessionId": session.id, "startTime": session.start_time.isoformat()}


async def stop_study_session(*, session_id: str, user_id: str) -> dict[str, Any]:
    """Stop a study session and update streak."""
    session = await progress_repo.find_session(session_id)
    if not session or session.user_id != user_id:
        from src.shared.exceptions import NotFoundError

        raise NotFoundError("StudySession", session_id)

    if session.end_time:
        return {"sessionId": session.id, "duration": session.duration, "message": "Already ended"}

    end_time = datetime.now(UTC)
    start_time = session.start_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    duration_minutes = (end_time - start_time).total_seconds() / 60

    await progress_repo.update_session(
        session_id, {"endTime": end_time, "duration": duration_minutes}
    )

    # Update streak
    await _update_streak(user_id, end_time)

    await emit(
        ProgressEvents.STUDY_SESSION_COMPLETED,
        {"user_id": user_id, "session_id": session_id, "duration_minutes": duration_minutes},
    )

    return {"sessionId": session_id, "duration": duration_minutes, "endTime": end_time.isoformat()}


async def get_streak(*, user_id: str) -> dict[str, Any]:
    """Get current streak info."""
    streak = await progress_repo.get_streak(user_id)
    if not streak:
        return {"currentStreak": 0, "longestStreak": 0, "lastStudyDate": None}
    return {
        "currentStreak": streak.current_streak or 0,
        "longestStreak": streak.longest_streak or 0,
        "lastStudyDate": streak.last_study_date.isoformat() if streak.last_study_date else None,
    }


async def list_achievements(*, user_id: str) -> list[dict[str, Any]]:
    """Get all unlocked achievements."""
    achievements = await progress_repo.list_achievements(user_id)
    return [
        {
            "id": a.id,
            "type": str(a.achievement_type),
            "title": a.title,
            "description": a.description or "",
            "icon": a.icon,
            "unlockedAt": a.unlocked_at.isoformat(),
            "metadata": a.metadata_json,
        }
        for a in achievements
    ]


async def _update_streak(user_id: str, study_datetime: datetime) -> None:
    """Update user's study streak."""
    study_date = study_datetime.date()
    streak = await progress_repo.get_streak(user_id)

    if not streak:
        await progress_repo.upsert_streak(
            user_id, {"currentStreak": 1, "longestStreak": 1, "lastStudyDate": study_datetime}
        )
        return

    if streak.last_study_date:
        last_date = (
            streak.last_study_date.date()
            if isinstance(streak.last_study_date, datetime)
            else streak.last_study_date
        )
        days_diff = (study_date - last_date).days
        if days_diff == 0:
            return
        elif days_diff == 1:
            new_streak = streak.current_streak + 1
        else:
            new_streak = 1
    else:
        new_streak = 1

    longest = max(streak.longest_streak or 0, new_streak)
    await progress_repo.upsert_streak(
        user_id,
        {"currentStreak": new_streak, "longestStreak": longest, "lastStudyDate": study_datetime},
    )
