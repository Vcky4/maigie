"""Bounded composition service for the authenticated Prepare dashboard.

Read-only. Owns no persistence: preparations, topics, and quiz sessions belong
to their own services, and all derived progress comes from `prep_readiness`, so
this surface and the Learn dashboard cannot disagree about a preparation.

Partial failure follows the Learn dashboard's policy: a section that cannot be
loaded is reported in `meta.degradedSections` and rendered as unavailable rather
than as empty, and only a total failure is an error.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import status

from src.shared.exceptions import MaigieError

from .. import models
from ..repository import personal_learning_repo as repo
from . import prep_readiness

logger = logging.getLogger(__name__)

# Only these are treated as active; completed preparations are history.
ACTIVE_STATUSES = ("SETUP", "IN_PROGRESS")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _days_until(exam_date: datetime, now: datetime) -> int | None:
    """Whole days until the target date, or `None` once it has passed."""
    delta = _as_utc(exam_date) - now
    if delta.total_seconds() < 0:
        return None
    return delta.days


def _log_source_failure(user_id: str, source: str, error: BaseException) -> None:
    logger.warning(
        "Prepare dashboard source unavailable",
        extra={"user_id": user_id, "source": source},
        exc_info=(type(error), error, error.__traceback__),
    )


async def _load_active_preparations(user_id: str, limit: int) -> tuple[list[Any], int]:
    """Active preparations ordered by target date, plus the total active count."""
    items, total = await repo.search_exam_preps(user_id, status="IN_PROGRESS", skip=0, take=limit)
    setup_items, setup_total = await repo.search_exam_preps(
        user_id, status="SETUP", skip=0, take=limit
    )
    combined = items + setup_items
    combined.sort(key=lambda prep: _as_utc(prep.exam_date))
    return combined[:limit], total + setup_total


async def get_dashboard(
    *,
    user_id: str,
    preparation_limit: int,
    topic_limit: int,
    session_limit: int,
) -> models.PrepareDashboardResponse:
    now = datetime.now(UTC)

    preparations_result, sessions_result = await asyncio.gather(
        _load_active_preparations(user_id, preparation_limit),
        repo.list_recent_quiz_sessions(user_id, take=session_limit),
        return_exceptions=True,
    )

    if isinstance(preparations_result, BaseException) and isinstance(
        sessions_result, BaseException
    ):
        _log_source_failure(user_id, "preparations", preparations_result)
        _log_source_failure(user_id, "sessions", sessions_result)
        raise MaigieError(
            "Prepare is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PREPARE_DASHBOARD_UNAVAILABLE",
        )

    degraded: set[models.PrepareDashboardSection] = set()

    preparations: list[Any] = []
    preparations_total = 0
    if isinstance(preparations_result, BaseException):
        # Without preparations there is nothing to summarise or focus on.
        degraded.update({"preparations", "summary", "focusTopics"})
        _log_source_failure(user_id, "preparations", preparations_result)
    else:
        preparations, preparations_total = preparations_result

    recent_sessions: list[Any] = []
    if isinstance(sessions_result, BaseException):
        degraded.add("recentSessions")
        _log_source_failure(user_id, "sessions", sessions_result)
    else:
        recent_sessions = sessions_result

    prep_ids = [preparation.id for preparation in preparations]
    subject_by_id = {preparation.id: preparation.subject for preparation in preparations}

    progress_by_prep: dict[str, prep_readiness.PrepProgress] = {}
    focus_topics: list[Any] = []
    if prep_ids:
        progress_result, topics_result = await asyncio.gather(
            prep_readiness.load_for_preparations(prep_ids),
            repo.list_weakest_prep_topics(prep_ids, take=topic_limit),
            return_exceptions=True,
        )
        if isinstance(progress_result, BaseException):
            degraded.update({"preparations", "summary"})
            _log_source_failure(user_id, "progress", progress_result)
        else:
            progress_by_prep = progress_result
        if isinstance(topics_result, BaseException):
            degraded.add("focusTopics")
            _log_source_failure(user_id, "focusTopics", topics_result)
        else:
            focus_topics = topics_result

    preparation_summaries = [
        models.PreparationProgressSummary(
            id=preparation.id,
            subject=preparation.subject,
            description=preparation.description,
            status=preparation.status,
            prep_type=preparation.prep_type,
            exam_date=preparation.exam_date,
            days_until_exam=_days_until(preparation.exam_date, now),
            progress_percent=progress.progress_percent,
            average_mastery_percent=progress.average_mastery_percent,
            topics_total=progress.topics_total,
            topics_strong=progress.topics_strong,
            topics_focus=progress.topics_focus,
            topics_assessed=progress.topics_assessed,
            questions_answered=progress.questions_answered,
            accuracy_percent=progress.accuracy_percent,
            quizzes_taken=progress.quizzes_taken,
            practice_ready=progress.practice_ready,
        )
        for preparation in preparations
        if (progress := progress_by_prep.get(preparation.id)) is not None
    ]

    answered = sum(progress.questions_answered for progress in progress_by_prep.values())
    correct = sum(progress.questions_correct for progress in progress_by_prep.values())
    practice_seconds = sum(progress.practice_seconds for progress in progress_by_prep.values())
    summary = models.PrepareSummaryStats(
        active_preparations=preparations_total,
        questions_answered=answered,
        accuracy_percent=(round((correct / answered) * 100, 1) if answered else None),
        practice_minutes=practice_seconds // 60,
        quizzes_taken=sum(progress.quizzes_taken for progress in progress_by_prep.values()),
    )

    section_order: list[models.PrepareDashboardSection] = [
        "summary",
        "preparations",
        "focusTopics",
        "recentSessions",
    ]

    return models.PrepareDashboardResponse(
        meta=models.PrepareDashboardMeta(
            generated_at=now,
            degraded_sections=[s for s in section_order if s in degraded],
        ),
        summary=summary,
        preparations=preparation_summaries,
        preparations_total=preparations_total,
        focus_topics=[
            models.PrepareFocusTopic(
                id=topic.id,
                preparation_id=topic.prep_id,
                preparation_subject=subject_by_id.get(topic.prep_id, ""),
                title=topic.title,
                mastery_percent=round(topic.mastery_score or 0.0, 1),
                band=prep_readiness.mastery_band(topic.mastery_score),
                order_index=topic.order_index,
            )
            for topic in focus_topics
        ],
        recent_sessions=[
            models.PrepareSessionSummary(
                id=quiz.id,
                preparation_id=quiz.prep_id,
                preparation_subject=subject_by_id.get(quiz.prep_id, ""),
                mode=quiz.mode,
                status=quiz.status,
                total_questions=quiz.total_questions,
                correct_count=quiz.correct_count,
                score_percent=quiz.score_percentage,
                duration_seconds=quiz.duration_seconds,
                completed_at=quiz.completed_at,
                created_at=quiz.created_at,
            )
            for quiz in recent_sessions
        ],
    )
