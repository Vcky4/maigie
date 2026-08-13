"""Bounded composition service for the authenticated Learn dashboard."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import status

from src.domains.knowledge.repository import knowledge_repo
from src.shared.exceptions import MaigieError

from .. import models
from ..repository import personal_learning_repo
from . import document_impl, flashcard_service, note_service, prep_readiness

logger = logging.getLogger(__name__)

#: Re-exported from the flashcard service, which owns it. Two surfaces quote a review
#: estimate and they must not be able to disagree about what a card costs.
REVIEW_SECONDS_PER_CARD = flashcard_service.REVIEW_SECONDS_PER_CARD


def _clamp_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _percent(completed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return _clamp_percent((completed / total) * 100)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _estimated_topic_minutes(estimated_hours: float | None) -> int | None:
    if estimated_hours is None:
        return None
    return max(0, round(estimated_hours * 60))


async def _load_courses(user_id: str, limit: int) -> tuple[list[Any], int, int, int]:
    (courses, total), (active_courses, completed_topics) = await asyncio.gather(
        knowledge_repo.list_courses(
            user_id,
            where={"archived": False},
            skip=0,
            take=limit,
            order={"updatedAt": "desc"},
        ),
        knowledge_repo.get_course_dashboard_stats(user_id),
    )
    return courses, total, active_courses, completed_topics


def _map_course(course: Any) -> models.LearnCourseSummary:
    modules = list(course.modules or [])
    topics = [topic for module in modules for topic in (module.topics or [])]
    completed = sum(1 for topic in topics if topic.completed)
    next_topic = next((topic for topic in topics if not topic.completed), None)
    next_topic_model = None
    if next_topic is not None:
        next_topic_model = models.LearnNextTopic(
            id=next_topic.id,
            title=next_topic.title,
            estimated_minutes=_estimated_topic_minutes(next_topic.estimated_hours),
        )
    return models.LearnCourseSummary(
        id=course.id,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        progress_percent=_percent(completed, len(topics)),
        completed_topics=completed,
        total_topics=len(topics),
        module_count=len(modules),
        next_topic=next_topic_model,
        updated_at=course.updated_at,
    )


async def _load_review(user_id: str) -> tuple[dict[str, Any], int]:
    stats, overdue = await asyncio.gather(
        flashcard_service.get_statistics(user_id=user_id),
        personal_learning_repo.count_overdue_flashcards(user_id),
    )
    return stats, overdue


async def _load_notes(user_id: str, limit: int) -> tuple[list[Any], int]:
    (notes, _), total = await asyncio.gather(
        note_service.list_notes(user_id=user_id, page=1, size=limit),
        personal_learning_repo.count_user_notes(user_id),
    )
    return notes, total


async def _load_paths(user_id: str, limit: int) -> tuple[list[models.LearnPathSummary], int]:
    (plans, plan_total), (preparations, prep_total) = await asyncio.gather(
        personal_learning_repo.list_dashboard_study_plans(user_id, take=limit),
        personal_learning_repo.list_dashboard_exam_preps(user_id, take=limit),
    )
    paths: list[models.LearnPathSummary] = []
    for plan in plans:
        completed = max(0, plan.completed_items or 0)
        total = max(0, plan.total_items or 0)
        paths.append(
            models.LearnPathSummary(
                entity_type="study_plan",
                id=plan.id,
                title=plan.title,
                description=plan.goal_description,
                status=plan.status,
                deadline=plan.deadline,
                completed_units=completed,
                total_units=total,
                progress_percent=_percent(completed, total),
            )
        )
    # Preparation progress comes from the shared mastery ladder, so this card and
    # the Prepare surface cannot report different numbers for the same
    # preparation. `progress_percent` is deliberately strong/total rather than
    # mean mastery, because the client renders it directly above an
    # "x / y complete" line and any other ratio would contradict it.
    progress_by_prep = await prep_readiness.load_for_preparations(
        [preparation.id for preparation in preparations]
    )
    for preparation in preparations:
        progress = progress_by_prep.get(preparation.id)
        paths.append(
            models.LearnPathSummary(
                entity_type="preparation",
                id=preparation.id,
                title=preparation.subject,
                description=preparation.description,
                status=preparation.status,
                deadline=preparation.exam_date,
                completed_units=progress.topics_strong if progress else 0,
                total_units=progress.topics_total if progress else 0,
                progress_percent=progress.progress_percent if progress else 0,
            )
        )
    paths.sort(
        key=lambda item: (
            _as_utc(item.deadline) if item.deadline else datetime.max.replace(tzinfo=UTC)
        )
    )
    # The total counts both kinds, because both are returned as paths. Reporting
    # `plan_total` alone made the number contradict the cards beside it: a learner
    # with two plans and three preparations saw five cards and a total of two.
    return paths[:limit], plan_total + prep_total


def _merge_recent_items(
    notes: list[Any], resources: list[Any], documents: list[Any], limit: int
) -> list[models.LearnRecentItem]:
    items: list[models.LearnRecentItem] = []
    items.extend(
        models.LearnRecentItem(
            entity_type="note",
            id=note.id,
            title=note.title,
            context_label="Personal note",
            occurred_at=note.updated_at,
        )
        for note in notes
    )
    items.extend(
        models.LearnRecentItem(
            entity_type="saved_resource",
            id=resource.id,
            title=resource.title,
            context_label=resource.source_type.replace("_", " ").title(),
            occurred_at=resource.last_accessed_at or resource.created_at,
        )
        for resource in resources
    )
    items.extend(
        models.LearnRecentItem(
            entity_type="document",
            id=document.id,
            title=document.title,
            context_label=(document.format or "Document").upper(),
            occurred_at=document.created_at,
        )
        for document in documents
    )
    items.sort(key=lambda item: _as_utc(item.occurred_at), reverse=True)
    return items[:limit]


def _log_source_failure(user_id: str, source: str, error: BaseException) -> None:
    logger.warning(
        "Learn dashboard source unavailable",
        extra={"user_id": user_id, "source": source},
        exc_info=(type(error), error, error.__traceback__),
    )


async def get_dashboard(
    *, user_id: str, course_limit: int, path_limit: int, recent_limit: int
) -> models.LearnDashboardResponse:
    results = await asyncio.gather(
        _load_courses(user_id, course_limit),
        _load_notes(user_id, recent_limit),
        personal_learning_repo.list_recent_resources(user_id, take=recent_limit),
        document_impl.list_documents(user_id=user_id, page=1, page_size=recent_limit),
        _load_review(user_id),
        _load_paths(user_id, path_limit),
        return_exceptions=True,
    )
    if all(isinstance(result, BaseException) for result in results):
        for source, error in zip(
            ("courses", "notes", "resources", "documents", "review", "paths"),
            results,
            strict=True,
        ):
            _log_source_failure(user_id, source, error)
        raise MaigieError(
            "Learning dashboard is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="LEARN_DASHBOARD_UNAVAILABLE",
        )

    degraded: set[models.LearnDashboardSection] = set()
    courses: list[Any] = []
    course_total = active_courses = completed_topics = 0
    notes: list[Any] = []
    note_total = 0
    resources: list[Any] = []
    resource_total = 0
    documents: list[Any] = []
    document_total = 0
    flashcard_stats: dict[str, Any] = {}
    overdue_cards = 0
    paths: list[models.LearnPathSummary] = []
    paths_total = 0  # Both study plans and preparations

    source_sections: dict[str, set[models.LearnDashboardSection]] = {
        "courses": {"courses", "stats", "tools"},
        "notes": {"stats", "tools", "recentItems"},
        "resources": {"stats", "tools", "recentItems"},
        "documents": {"stats", "tools", "recentItems"},
        "review": {"review", "tools"},
        "paths": {"paths", "tools"},
    }
    for source, result in zip(source_sections, results, strict=True):
        if isinstance(result, BaseException):
            degraded.update(source_sections[source])
            _log_source_failure(user_id, source, result)

    if not isinstance(results[0], BaseException):
        courses, course_total, active_courses, completed_topics = results[0]
    if not isinstance(results[1], BaseException):
        notes, note_total = results[1]
    if not isinstance(results[2], BaseException):
        resources, resource_total = results[2]
    if not isinstance(results[3], BaseException):
        documents, document_total = results[3]
    if not isinstance(results[4], BaseException):
        flashcard_stats, overdue_cards = results[4]
    if not isinstance(results[5], BaseException):
        paths, paths_total = results[5]

    due_cards = max(0, int(flashcard_stats.get("dueToday", 0)))
    total_cards = max(0, int(flashcard_stats.get("total", 0)))
    mastered_cards = max(0, int(flashcard_stats.get("masteredCount", 0)))
    mastery = _percent(mastered_cards, total_cards) if total_cards else None
    review = models.LearnReviewSummary(
        due_cards=due_cards,
        overdue_cards=max(0, overdue_cards),
        estimated_minutes=(due_cards * REVIEW_SECONDS_PER_CARD + 59) // 60,
        mastery_percent=mastery,
    )
    stats = models.LearnDashboardStats(
        active_courses=max(0, active_courses),
        completed_topics=max(0, completed_topics),
        saved_resources=max(0, resource_total),
        personal_notes=max(0, note_total),
        generated_documents=max(0, document_total),
    )
    tools = [
        models.LearnToolSummary(type="course", count=max(0, course_total)),
        models.LearnToolSummary(type="note", count=max(0, note_total)),
        models.LearnToolSummary(type="flashcard", count=total_cards),
        models.LearnToolSummary(type="saved_resource", count=max(0, resource_total)),
        models.LearnToolSummary(type="document", count=max(0, document_total)),
        models.LearnToolSummary(type="study_plan", count=max(0, paths_total)),
    ]
    recent_items = _merge_recent_items(notes, resources, documents, recent_limit)
    section_order = [
        "featured",
        "review",
        "stats",
        "courses",
        "paths",
        "tools",
        "recentItems",
        "collections",
    ]
    return models.LearnDashboardResponse(
        meta=models.LearnDashboardMeta(
            generated_at=datetime.now(UTC),
            degraded_sections=[section for section in section_order if section in degraded],
        ),
        featured=None,
        review=review,
        stats=stats,
        courses=models.LearnCourseList(
            items=[_map_course(course) for course in courses],
            total=max(0, course_total),
        ),
        paths=paths,
        tools=tools,
        recent_items=recent_items,
        collections=[],
    )
