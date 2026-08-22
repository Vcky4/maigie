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
    """The course page plus the two course stats.

    `total` is reused as `activeCourses` rather than counted twice. Both numbers are
    `count(*) FROM "Course" WHERE userId = ? AND NOT archived` — `list_courses` renders it as
    `archived = false` from its `where` dict and `get_course_dashboard_stats` rendered it as
    `archived IS false` from `.is_(False)`, which is why two identical counts in one request did not
    look like duplicates in the log. Same question, two spellings, two round trips.
    """
    (courses, total), completed_topics = await asyncio.gather(
        knowledge_repo.list_courses(
            user_id,
            where={"archived": False},
            skip=0,
            take=limit,
            order={"updatedAt": "desc"},
        ),
        knowledge_repo.count_completed_topics(user_id),
    )
    return courses, total, total, completed_topics


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


async def _load_featured(
    user_id: str, courses_task: asyncio.Task[tuple[list[Any], int, int, int]] | None = None
) -> models.LearnFeaturedItem | None:
    """The thing to resume: the next unfinished topic of the course last made progress in.

    `featured` returned `null` unconditionally, with §7.1 recording that as deliberate "until a
    persisted last-position source exists". The source arrived in stage 4 — migration 024 put
    `completedAt` on the topic, and §18 recorded the decision as resolved — but only the *courses*
    dashboard was wired to it. So the course library grew a resume card and the Learn dashboard, which
    is the page a learner reaches Learn through, kept returning nothing and no longer had a reason to.

    "Most recent completion" rather than most recently *updated*: renaming a course would otherwise
    promote it above the one actually being worked through. A learner with no completions has nothing
    to resume and gets no card, rather than an arbitrary course.

    Not read *only* out of the course page loaded beside it: that page is the four most recently
    updated courses, so a resume target outside it would silently produce no card. But when the
    course is already there — the common case, since the course you last completed a topic in is
    usually one you recently updated — it is reused rather than refetched. The refetch is not cheap:
    it reloads the course with every module, every topic, and (through the relationship defaults)
    every topic's lesson sections.
    """
    # Started before the course page is awaited, so the two still overlap: this query does not depend
    # on the courses and blocking on them first would serialise two independent reads.
    recent = await knowledge_repo.recently_completed_topics(user_id, limit=1)
    if not recent:
        return None

    _, course_id, _ = recent[0]

    loaded_courses: list[Any] = []
    if courses_task is not None:
        try:
            loaded_courses = (await courses_task)[0]
        except Exception:
            # The course branch failed. It reports its own degradation; featured falls back to
            # fetching rather than disappearing because a neighbour broke.
            loaded_courses = []

    course = next((item for item in loaded_courses if item.id == course_id), None)
    if course is None:
        # The outline variant: this needs topic titles and completion flags, not lesson bodies.
        course = await knowledge_repo.find_course_outline(course_id, user_id)
    if course is None:
        return None

    topics = [topic for module in (course.modules or []) for topic in (module.topics or [])]
    completed = sum(1 for topic in topics if topic.completed)
    next_topic = next((topic for topic in topics if not topic.completed), None)

    # A finished course is featured as the course. The alternative — no card once the last topic is
    # completed — hides the course the learner was working in at the moment they finish it.
    if next_topic is None:
        return models.LearnFeaturedItem(
            entity_type="course",
            entity_id=course.id,
            course_id=course.id,
            topic_id=None,
            title=course.title,
            description=course.description,
            course_title=course.title,
            estimated_minutes=None,
            progress_percent=_percent(completed, len(topics)),
            completed_units=completed,
            total_units=len(topics),
        )

    return models.LearnFeaturedItem(
        entity_type="topic",
        entity_id=next_topic.id,
        course_id=course.id,
        topic_id=next_topic.id,
        title=next_topic.title,
        description=next_topic.summary,
        course_title=course.title,
        estimated_minutes=_estimated_topic_minutes(next_topic.estimated_hours),
        progress_percent=_percent(completed, len(topics)),
        completed_units=completed,
        total_units=len(topics),
    )


async def _load_review(user_id: str) -> tuple[dict[str, Any], int]:
    """Flashcard figures for the review card.

    One call, not two. `count_overdue_flashcards` used to run beside this — a second round trip on a
    second connection, asking the same table an almost identical question (due before midnight rather
    than due by now). The two remain different figures and both are still shown; they are now two
    filtered aggregates in the statistics query rather than two queries.
    """
    stats = await flashcard_service.get_statistics(user_id=user_id)
    return stats, int(stats.get("overdueCount") or 0)


async def _load_notes(user_id: str, limit: int) -> tuple[list[Any], int]:
    """A page of recent notes and the note total.

    One call. `list_notes` already returns its own total and that total was being **discarded** —
    `(notes, _)` — in favour of `count_user_notes` running beside it, so the request paid for two
    counts and used the second.

    Using the first also corrects the number. `count_user_notes` counts every unarchived note
    including space-scoped ones, while the list it sits next to is filtered to `spaceId IS NULL`;
    a learner with notes in a space saw a total larger than the library it described.
    """
    notes, total = await note_service.list_notes(user_id=user_id, page=1, size=limit)
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


async def _load_collections(user_id: str) -> list[dict]:
    """Auto-seed and return dashboard collection summaries."""
    from . import collection_service

    return await collection_service.get_dashboard_collections(user_id)


async def get_dashboard(
    *, user_id: str, course_limit: int, path_limit: int, recent_limit: int
) -> models.LearnDashboardResponse:
    # The course page is a task rather than a bare coroutine so `_load_featured` can await the same
    # result instead of refetching the course it usually already contains. It still runs concurrently
    # with everything else, and `gather` still reports its failure in its own slot.
    courses_task = asyncio.create_task(_load_courses(user_id, course_limit))

    # Two waves of four, not one flat gather of eight.
    #
    # These loaders do not share a connection: `document_impl`, `collection_service` and
    # `personal_learning_repo` each acquire their own. Eight at once exhausted the session-mode pooler
    # outright when Reflect's equivalent dashboard was first run against the real database
    # (`EMAXCONNSESSION`, 15 clients), degrading sections that had nothing wrong with them. This
    # endpoint has always had the same shape and the same exposure; it simply had not been the one to
    # hit the ceiling. Halving the peak keeps the composition concurrent where it matters.
    #
    # The split is the page's spine first, then its side rails, so if the budget were ever exceeded
    # again the sections that would suffer are the peripheral ones. `featured` is deliberately in the
    # second wave: it awaits `courses_task`, which the first wave has already resolved, so it costs no
    # extra query and cannot start a duplicate one.
    wave_one = await asyncio.gather(
        courses_task,
        _load_notes(user_id, recent_limit),
        _load_review(user_id),
        _load_paths(user_id, path_limit),
        return_exceptions=True,
    )
    wave_two = await asyncio.gather(
        personal_learning_repo.list_recent_resources(user_id, take=recent_limit),
        document_impl.list_documents(user_id=user_id, page=1, page_size=recent_limit),
        _load_featured(user_id, courses_task),
        _load_collections(user_id),
        return_exceptions=True,
    )

    # Keyed by source rather than read back positionally. The previous version indexed `results[0]`
    # through `results[7]` while a separate dict supplied the section names in a matching order — two
    # orderings that had to agree, silently, and splitting the gather would have broken that agreement
    # with no signal beyond the wrong data in the wrong field.
    outcomes: dict[str, Any] = dict(
        zip(("courses", "notes", "review", "paths"), wave_one, strict=True)
    )
    outcomes.update(
        zip(("resources", "documents", "featured", "collections"), wave_two, strict=True)
    )

    if all(isinstance(result, BaseException) for result in outcomes.values()):
        for source, error in outcomes.items():
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
    featured: models.LearnFeaturedItem | None = None
    collections: list[models.LearnCollectionSummary] = []

    source_sections: dict[str, set[models.LearnDashboardSection]] = {
        "courses": {"courses", "stats", "tools"},
        "notes": {"stats", "tools", "recentItems"},
        "resources": {"stats", "tools", "recentItems"},
        "documents": {"stats", "tools", "recentItems"},
        "review": {"review", "tools"},
        "paths": {"paths", "tools"},
        # Only its own section. A failed resume lookup must not mark the course grid degraded — the
        # grid loaded, and telling a learner their courses are unavailable while showing them is
        # worse than one missing card.
        "featured": {"featured"},
        "collections": {"collections"},
    }
    # Every source named in `source_sections` must have been gathered, and vice versa. Asserted rather
    # than assumed: adding a loader to one and forgetting the other is the mistake this shape invites.
    assert outcomes.keys() == source_sections.keys(), (
        f"gathered {sorted(outcomes)} but mapped {sorted(source_sections)}"
    )

    for source, result in outcomes.items():
        if isinstance(result, BaseException):
            degraded.update(source_sections[source])
            _log_source_failure(user_id, source, result)

    if not isinstance(outcomes["courses"], BaseException):
        courses, course_total, active_courses, completed_topics = outcomes["courses"]
    if not isinstance(outcomes["notes"], BaseException):
        notes, note_total = outcomes["notes"]
    if not isinstance(outcomes["resources"], BaseException):
        resources, resource_total = outcomes["resources"]
    if not isinstance(outcomes["documents"], BaseException):
        documents, document_total = outcomes["documents"]
    if not isinstance(outcomes["review"], BaseException):
        flashcard_stats, overdue_cards = outcomes["review"]
    if not isinstance(outcomes["paths"], BaseException):
        paths, paths_total = outcomes["paths"]
    if not isinstance(outcomes["featured"], BaseException):
        featured = outcomes["featured"]
    if not isinstance(outcomes["collections"], BaseException):
        collections = [
            models.LearnCollectionSummary(
                id=c["id"],
                title=c["title"],
                item_count=c["item_count"],
                entity_types=c["entity_types"],
            )
            for c in outcomes["collections"]
        ]

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
        featured=featured,
        review=review,
        stats=stats,
        courses=models.LearnCourseList(
            items=[_map_course(course) for course in courses],
            total=max(0, course_total),
        ),
        paths=paths,
        tools=tools,
        recent_items=recent_items,
        collections=collections,
    )
