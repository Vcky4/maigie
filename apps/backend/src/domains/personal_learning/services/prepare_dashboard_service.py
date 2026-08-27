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
from . import prep_focus, prep_readiness

logger = logging.getLogger(__name__)

# Statuses that put a preparation on the dashboard, because it still wants something from the learner.
# Only `COMPLETED` is left off: a reviewed preparation is history.
#
# `AWAITING_REVIEW` is here because it wants the most. Its exam has happened and the learner has not said
# how it went — and their answer is what completes the preparation, so hiding it is how the question never
# gets answered. Omitting it would have taken the preparation off this list the morning after the exam and
# left the review reachable only from a notification, which quiet hours and the daily cap can both suppress.
ACTIVE_STATUSES = ("SETUP", "IN_PROGRESS", "AWAITING_REVIEW")

# Cap on the topics loaded to compute per-preparation recommendations. Weakest
# first, so the topic a recommendation would choose is always inside the window
# even when a preparation has more topics than this.
_MAX_TOPICS_FOR_RECOMMENDATION = 200


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
    """Preparations still wanting something from the learner, by exam date, plus the total count.

    Driven by `ACTIVE_STATUSES` rather than by two hard-coded strings, which is what it was: the constant
    existed and the queries did not use it, so adding a third status changed the constant and the list
    disagreed with it. A test asserts the queries issued match the constant, and that is what caught it.

    One query per status because `search_exam_preps` takes a single one. Read sequentially rather than
    gathered, matching `agenda_service`: fanning out across sessions is what exhausted the session-mode
    pooler and made `daily-counts` return intermittent 500s.
    """
    combined: list[Any] = []
    total = 0
    # `prep_status`, not `status`: this module imports `status` from fastapi, and shadowing it in a loop
    # works until someone adds a status-code reference inside the body.
    for prep_status in ACTIVE_STATUSES:
        items, count = await repo.search_exam_preps(user_id, status=prep_status, skip=0, take=limit)
        combined.extend(items)
        total += count

    combined.sort(key=lambda prep: _as_utc(prep.exam_date))
    return combined[:limit], total


def _recommendation(
    topics: list[Any], answered_by_topic: dict[str, int]
) -> models.PrepFocusRecommendation:
    """Adapt `prep_focus` onto the wire model.

    Kept here rather than in `prep_focus` so that module stays free of response
    schemas and can be unit-tested without importing them.
    """
    focus = prep_focus.recommend(topics, answered_by_topic=answered_by_topic)
    return models.PrepFocusRecommendation(
        topic_id=focus.topic_id,
        topic_title=focus.topic_title,
        mastery_percent=focus.mastery_percent,
        band=focus.band,
        reason_code=focus.reason_code,
        reason=focus.reason,
        recommended_mode=focus.recommended_mode,
        recommended_question_count=focus.recommended_question_count,
        estimated_minutes=focus.estimated_minutes,
    )


def _milestone_status(item: Any, now: datetime) -> str:
    """Map a study-plan item onto the three states a timeline rail shows.

    Derived rather than stored: `COMPLETE` is the item's own status, and `TODAY`
    versus `UPCOMING` is a question about the current date, which a stored value
    would answer wrongly by tomorrow.
    """
    if item.status in ("COMPLETED", "COMPLETE") or item.completed_at is not None:
        return "COMPLETE"
    scheduled = _as_utc(item.scheduled_date).date()
    today = now.date()
    if scheduled < today:
        return "OVERDUE"
    if scheduled == today:
        return "TODAY"
    return "UPCOMING"


async def get_dashboard(
    *,
    user_id: str,
    preparation_limit: int,
    topic_limit: int,
    session_limit: int,
    milestone_limit: int = 6,
) -> models.PrepareDashboardResponse:
    now = datetime.now(UTC)

    preparations_result, sessions_result, streak_result = await asyncio.gather(
        _load_active_preparations(user_id, preparation_limit),
        repo.list_recent_quiz_sessions(user_id, take=session_limit),
        prep_readiness.load_practice_streak(user_id),
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

    # A streak the learner does not have is not worth degrading the summary over,
    # so a failure here reports the section degraded and leaves the value unknown
    # rather than claiming zero.
    practice_streak: int | None = None
    if isinstance(streak_result, BaseException):
        degraded.add("summary")
        _log_source_failure(user_id, "practiceStreak", streak_result)
    else:
        practice_streak = streak_result

    prep_ids = [preparation.id for preparation in preparations]
    subject_by_id = {preparation.id: preparation.subject for preparation in preparations}

    progress_by_prep: dict[str, prep_readiness.PrepProgress] = {}
    focus_topics: list[Any] = []
    # Every topic of every active preparation, needed to recommend a next action
    # per preparation. The dashboard's `focusTopics` list cannot serve that: it is
    # bounded across all preparations, so a preparation may be absent from it
    # entirely and would otherwise get no recommendation at all.
    all_topics: list[Any] = []
    topic_counts: dict[str, dict[str, int]] = {}
    milestone_rows: list[Any] = []
    if prep_ids:
        (
            progress_result,
            topics_result,
            all_topics_result,
            counts_result,
            milestones_result,
        ) = await asyncio.gather(
            prep_readiness.load_for_preparations(prep_ids),
            repo.list_weakest_prep_topics(prep_ids, take=topic_limit),
            repo.list_weakest_prep_topics(prep_ids, take=_MAX_TOPICS_FOR_RECOMMENDATION),
            repo.get_prep_topic_question_counts(prep_ids),
            repo.list_prep_milestone_items(prep_ids, user_id, take=milestone_limit),
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
        if isinstance(all_topics_result, BaseException):
            # Without topics there is no recommendation, but the cards are still
            # worth rendering, so this degrades `preparations` rather than failing.
            degraded.add("preparations")
            _log_source_failure(user_id, "recommendations", all_topics_result)
        else:
            all_topics = all_topics_result
        if isinstance(counts_result, BaseException):
            _log_source_failure(user_id, "topicCounts", counts_result)
        else:
            topic_counts = counts_result
        if isinstance(milestones_result, BaseException):
            degraded.add("milestones")
            _log_source_failure(user_id, "milestones", milestones_result)
        else:
            milestone_rows = milestones_result

    answered_by_topic = {
        topic_id: counts.get("answered_count", 0) for topic_id, counts in topic_counts.items()
    }
    topics_by_prep: dict[str, list[Any]] = {}
    for topic in all_topics:
        topics_by_prep.setdefault(topic.prep_id, []).append(topic)

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
            target_readiness=preparation.target_readiness,
            topics_total=progress.topics_total,
            topics_strong=progress.topics_strong,
            topics_focus=progress.topics_focus,
            topics_assessed=progress.topics_assessed,
            questions_answered=progress.questions_answered,
            accuracy_percent=progress.accuracy_percent,
            quizzes_taken=progress.quizzes_taken,
            practice_minutes=progress.practice_minutes,
            practice_ready=progress.practice_ready,
            next_action=_recommendation(topics_by_prep.get(preparation.id, []), answered_by_topic),
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
        practice_streak=practice_streak,
    )

    section_order: list[models.PrepareDashboardSection] = [
        "summary",
        "preparations",
        "focusTopics",
        "recentSessions",
        "milestones",
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
                category=topic.category,
                mastery_percent=round(topic.mastery_score or 0.0, 1),
                target_mastery=topic.target_mastery,
                band=prep_readiness.mastery_band(topic.mastery_score),
                order_index=topic.order_index,
                question_count=topic_counts.get(topic.id, {}).get("question_count", 0),
                answered_question_count=topic_counts.get(topic.id, {}).get("answered_count", 0),
            )
            for topic in focus_topics
        ],
        milestones=[
            models.PrepareMilestone(
                id=item.id,
                preparation_id=item_prep_id,
                preparation_subject=subject_by_id.get(item_prep_id, ""),
                kind="STUDY",
                title=item.title,
                detail=item.description,
                scheduled_for=item.scheduled_date,
                status=_milestone_status(item, now),
                estimated_minutes=item.estimated_minutes,
                prep_topic_id=item.prep_topic_id,
            )
            for item, item_prep_id in milestone_rows
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
