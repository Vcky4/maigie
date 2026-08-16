"""
Knowledge domain — API routes.

Endpoints for courses (CRUD, progress, AI generation),
modules, topics, and resources.

Mounted at: /api/v1/knowledge
"""

import asyncio
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from src.shared.auth import CurrentUser

from . import models
from .repository import knowledge_repo
from .services import course_service, lesson_service, resource_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge"])


# ===========================================================================
# AI Course Generation
# ===========================================================================


@router.post("/courses/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_course(
    body: models.AICourseRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
):
    """Trigger AI course generation (returns immediately, updates via WebSocket)."""
    # AI course generation pending migration to new LLM architecture
    raise HTTPException(status_code=501, detail="AI course generation pending migration")


# ===========================================================================
# Courses
# ===========================================================================


def _progress_percent(completed: int, total: int) -> float:
    """Completion as a percentage, to one decimal place.

    Rounded the same way and to the same precision as `course_service.calculate_course_progress`, so
    a course's figure does not change depending on whether it was read from the library or the detail
    page. A course with no topics is 0%, not a division by zero.
    """
    if not total:
        return 0.0
    return round(completed / total * 100, 1)


async def _to_list_items(courses: list[Any]) -> list[models.CourseListItem]:
    """Build library cards for a set of courses, with their derived figures.

    One function, used by both the list and the dashboard's featured card, so the same course cannot
    show one progress figure in the grid and a different one in the hero.

    Three grouped queries for the whole set, rather than a progress call per course that issued its own
    module query underneath. A course missing from one of the maps reads as "nothing to say" rather
    than as zero, which the card draws differently: no next topic means finished, and no remaining
    hours means the work was never sized.
    """
    if not courses:
        return []

    course_ids = [c.id for c in courses]
    totals, next_topics, remaining = await asyncio.gather(
        knowledge_repo.course_progress_totals(course_ids),
        knowledge_repo.next_topics(course_ids),
        knowledge_repo.remaining_hours(course_ids),
    )

    items: list[models.CourseListItem] = []
    for c in courses:
        module_count, total_topics, completed_topics = totals.get(c.id, (0, 0, 0))
        next_topic = next_topics.get(c.id)
        # snake_case ORM attributes, camelCase columns. Every course route read these
        # the wrong way and answered `500`; see the note in `get_course`.
        items.append(
            models.CourseListItem(
                id=c.id,
                userId=c.user_id,
                title=c.title,
                description=c.description,
                difficulty=c.difficulty,
                targetDate=c.target_date,
                isAIGenerated=c.is_ai_generated,
                archived=c.archived,
                progress=_progress_percent(completed_topics, total_topics),
                totalTopics=total_topics,
                completedTopics=completed_topics,
                moduleCount=module_count,
                nextTopic=(
                    models.CourseNextTopic(
                        id=next_topic.id,
                        moduleId=next_topic.module_id,
                        title=next_topic.title,
                        estimatedHours=next_topic.estimated_hours,
                    )
                    if next_topic
                    else None
                ),
                remainingHours=remaining.get(c.id),
                category=c.category,
                tags=c.tags,
                createdAt=c.created_at,
                updatedAt=c.updated_at,
            )
        )
    return items


@router.get("/courses", response_model=models.CourseListResponse)
async def list_courses(
    current_user: CurrentUser,
    archived: bool | None = Query(None),
    difficulty: str | None = Query(None),
    isAIGenerated: bool | None = Query(None),
    search: str | None = Query(None, max_length=255),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sortBy: str = Query("createdAt", pattern="^(createdAt|updatedAt|title)$"),
    sortOrder: str = Query("desc", pattern="^(asc|desc)$"),
    spaceId: str | None = Query(None),
):
    """List courses with pagination and filtering.

    Three things this used to get wrong, all of which the library page depends on.

    `search` was accepted and thrown away. It was translated into a Prisma-style `where["OR"]`, and
    the SQLAlchemy translator has no `OR` branch, so the parameter was silently dropped and the
    caller was handed the unfiltered library while believing it had searched.

    `archived` defaulted to *no filter*, so a shelved course still appeared in the default library.
    Archiving is the learner saying "not now"; a list that ignores that has ignored the only thing
    archiving does. It now defaults to unarchived, and `archived=true` is the archive view.

    `spaceId` was forced to `None` whenever it was absent, which is not the same as being unset: a
    course belonging to a space could not be listed at all, and there was no way to ask for
    everything. Absent now means "no filter"; `spaceId=""` asks for personal courses specifically.
    """
    where: dict[str, Any] = {}
    if spaceId is not None:
        # An empty string is how a caller asks for courses that belong to no space, since a query
        # parameter cannot carry a null. Left out entirely, both personal and space courses match.
        where["spaceId"] = spaceId or None
    # Defaults to the unarchived library rather than everything.
    where["archived"] = False if archived is None else archived
    if difficulty:
        where["difficulty"] = difficulty.upper()
    if isAIGenerated is not None:
        where["isAIGenerated"] = isAIGenerated
    if search:
        where["search"] = search

    skip = (page - 1) * pageSize
    courses, total = await knowledge_repo.list_courses(
        current_user.id, where=where, skip=skip, take=pageSize, order={sortBy: sortOrder}
    )

    items = await _to_list_items(courses)
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.CourseListResponse(
        items=items, total=total, page=page, pageSize=pageSize, pages=pages
    )


@router.get("/topics/{topic_id}", response_model=models.TopicLocationResponse)
async def get_topic(topic_id: str, current_user: CurrentUser):
    """One topic, with the module and course it belongs to.

    Added because a topic id was a dead end. Ownership is checked through the course, so another
    learner's topic is a `403` from `check_topic_ownership` — and a topic that does not exist is a
    `404`, which is the same answer as one in a course the caller cannot see.

    This is what lets a caller holding only a topic id open it: the study surface needs the course as
    well as the topic, and until now nothing could get from one to the other.
    """
    topic, module, course = await course_service.check_topic_ownership(topic_id, current_user.id)
    position, total = await knowledge_repo.topic_position(course.id, topic_id)

    return models.TopicLocationResponse(
        topic=models.TopicResponse.model_validate(topic, from_attributes=True),
        moduleId=module.id,
        moduleTitle=module.title,
        courseId=course.id,
        courseTitle=course.title,
        position=position,
        totalTopics=total,
        # Read from the stored column rather than recomputed here. It is kept true by
        # `recount_course_progress` on every event that can change it, and recomputing would mean two
        # expressions for one number that can disagree in the last digit.
        courseProgress=course.progress or 0.0,
    )


# ---------------------------------------------------------------------------
# Topic sections
#
# A lesson is a topic read one section at a time, and these are what make that possible. They sit
# under the topic rather than under the course path used by module and topic writes, because a
# section is only ever addressed through the topic that owns it and repeating the course and module
# ids in the path would let a caller send three ids that disagree.
# ---------------------------------------------------------------------------


@router.get("/topics/{topic_id}/sections", response_model=list[models.TopicSectionResponse])
async def list_topic_sections(topic_id: str, current_user: CurrentUser):
    """The sections of a topic, in reading order.

    A bare list rather than a pagination envelope: a lesson has a handful of sections, every caller
    wants all of them, and the reader cannot render a partial lesson — paging it would let the
    next/previous controls run off the end of a page.
    """
    await course_service.check_topic_ownership(topic_id, current_user.id)
    sections = await knowledge_repo.list_topic_sections(topic_id)
    return [
        models.TopicSectionResponse.model_validate(section, from_attributes=True)
        for section in sections
    ]


@router.post(
    "/topics/{topic_id}/sections",
    response_model=models.TopicSectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_section(
    topic_id: str, body: models.TopicSectionCreate, current_user: CurrentUser
):
    """Add one section to a topic."""
    await course_service.check_topic_ownership(topic_id, current_user.id)
    section = await knowledge_repo.create_topic_section(
        {**body.model_dump(mode="json", exclude_unset=True), "topicId": topic_id}
    )
    return models.TopicSectionResponse.model_validate(section, from_attributes=True)


@router.put(
    "/topics/{topic_id}/sections",
    response_model=list[models.TopicSectionResponse],
)
async def replace_topic_sections(
    topic_id: str, body: list[models.TopicSectionCreate], current_user: CurrentUser
):
    """Replace a topic's sections wholesale.

    A replace rather than an append, because the caller is generation rewriting a lesson, and
    appending would leave the new body after the old one with the learner scrolling through two
    versions. Deleting and inserting in that order means a failed insert leaves no sections rather
    than a mixture of both, which is the more obvious failure and the one the reader already handles
    by falling back to `Topic.content`.

    **This discards per-section completion for the topic**, because the sections it belonged to no
    longer exist. That is the honest outcome of replacing the body: progress through a lesson that has
    been rewritten does not transfer, and silently carrying it across by position would tell the
    learner they had read paragraphs that were not there when they read it.
    """
    await course_service.check_topic_ownership(topic_id, current_user.id)
    await knowledge_repo.delete_topic_sections(topic_id)
    await knowledge_repo.create_topic_sections(
        topic_id, [item.model_dump(mode="json", exclude_unset=True) for item in body]
    )
    sections = await knowledge_repo.list_topic_sections(topic_id)
    return [
        models.TopicSectionResponse.model_validate(section, from_attributes=True)
        for section in sections
    ]


async def _owned_section(section_id: str, user_id: str):
    """Resolve a section and prove the caller owns the course it sits in.

    Ownership runs through the topic, so a section in another learner's course is refused by the same
    check that refuses the topic. Written once because three routes need it and an ownership check
    that is easy to omit eventually is.
    """
    section = await knowledge_repo.find_topic_section(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")
    await course_service.check_topic_ownership(section.topic_id, user_id)
    return section


@router.put("/topics/{topic_id}/sections/{section_id}", response_model=models.TopicSectionResponse)
async def update_topic_section(
    topic_id: str, section_id: str, body: models.TopicSectionUpdate, current_user: CurrentUser
):
    """Edit one section."""
    section = await _owned_section(section_id, current_user.id)
    if section.topic_id != topic_id:
        raise HTTPException(status_code=400, detail="Section path mismatch")

    updated = await knowledge_repo.update_topic_section(
        section_id, body.model_dump(mode="json", exclude_unset=True)
    )
    return models.TopicSectionResponse.model_validate(updated, from_attributes=True)


@router.delete("/topics/{topic_id}/sections/{section_id}", status_code=204)
async def delete_topic_section(topic_id: str, section_id: str, current_user: CurrentUser):
    """Remove one section."""
    section = await _owned_section(section_id, current_user.id)
    if section.topic_id != topic_id:
        raise HTTPException(status_code=400, detail="Section path mismatch")
    await knowledge_repo.delete_topic_section(section_id)


@router.patch(
    "/topics/{topic_id}/sections/{section_id}/complete",
    response_model=models.TopicSectionResponse,
)
async def complete_topic_section(
    topic_id: str,
    section_id: str,
    current_user: CurrentUser,
    completed: bool = Query(True),
):
    """Mark a section worked through, or reopen it.

    `completed` defaults to true, unlike the topic toggle which requires it. Advancing through a
    lesson is the overwhelmingly common call and the reader sends it on every Continue; reopening is
    rare and deliberate, so it is the one that has to say so.

    **This does not touch `Topic.completed`, and finishing the last section does not complete the
    topic.** The two are different claims: that the learner read every section, and that they consider
    the topic done. Deriving one from the other would either mark a topic complete the moment the
    reader scrolled to the end — before any check was answered — or reopen a topic the learner had
    explicitly finished because a section was later added to it. Course progress therefore does not
    move here, which is why `recount_course_progress` is not called.
    """
    section = await _owned_section(section_id, current_user.id)
    if section.topic_id != topic_id:
        raise HTTPException(status_code=400, detail="Section path mismatch")

    updated = await knowledge_repo.set_topic_section_completed(section_id, completed)
    return models.TopicSectionResponse.model_validate(updated, from_attributes=True)


# ---------------------------------------------------------------------------
# Course ratings
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/rating", response_model=models.CourseRatingSummary)
async def get_course_rating(course_id: str, current_user: CurrentUser):
    """The course's aggregate rating, and this learner's own."""
    await course_service.check_course_ownership(course_id, current_user.id)
    average, count, mine = await knowledge_repo.course_rating_summary(course_id, current_user.id)
    return models.CourseRatingSummary(average=average, count=count, yourRating=mine)


@router.put("/courses/{course_id}/rating", response_model=models.CourseRatingSummary)
async def rate_course(course_id: str, body: models.CourseRatingCreate, current_user: CurrentUser):
    """Rate a course, or change an existing rating.

    `PUT`, not `POST`: one learner has at most one rating per course, so submitting twice sets the
    same resource rather than creating a second. Returns the recomputed aggregate rather than the
    single row, because the caller's next act is to render the average it just changed.
    """
    await course_service.check_course_ownership(course_id, current_user.id)
    await knowledge_repo.rate_course(course_id, current_user.id, body.value, body.comment)
    average, count, mine = await knowledge_repo.course_rating_summary(course_id, current_user.id)
    return models.CourseRatingSummary(average=average, count=count, yourRating=mine)


@router.get("/courses/dashboard", response_model=models.CoursesDashboardResponse)
async def get_courses_dashboard(current_user: CurrentUser):
    """Everything the course library shows above its grid, in one request.

    Declared before `/courses/{course_id}` so FastAPI does not read "dashboard" as a course id.

    Two figures come from outside this domain and are labelled as such rather than folded in silently:
    `flashcardsDue` is the review queue, and it is named for that. The rest is derived from the
    learner's own topics.
    """
    from src.domains.personal_learning.services import flashcard_service

    summary = await course_service.get_dashboard(user_id=current_user.id)

    # The course to resume: the one whose most recent topic completion is newest. Read from the
    # activity list rather than by a separate query, since that list is already ordered by exactly
    # that. A learner with no completions has no course to resume, and gets no featured card rather
    # than an arbitrary one.
    recent = summary.pop("recent")
    featured_item = None
    if recent:
        featured_course = await knowledge_repo.find_course(recent[0][1], current_user.id)
        if featured_course:
            featured_item = (await _to_list_items([featured_course]))[0]

    try:
        stats = await flashcard_service.get_statistics(user_id=current_user.id)
        flashcards_due = int(stats.get("dueToday") or 0)
    except Exception:
        # A failing review queue must not take the course library down with it. The count is a
        # pointer to other work, not a fact about courses, so zero is a tolerable fallback here in a
        # way it would not be for the figures above.
        logger.warning("Flashcard stats unavailable for courses dashboard", exc_info=True)
        flashcards_due = 0

    return models.CoursesDashboardResponse(
        activeCourses=summary["activeCourses"],
        archivedCourses=summary["archivedCourses"],
        totalTopics=summary["totalTopics"],
        completedTopics=summary["completedTopics"],
        weeklyHours=summary["weeklyHours"],
        weeklyTopicsCompleted=summary["weeklyTopicsCompleted"],
        currentStreakDays=summary["currentStreakDays"],
        flashcardsDue=flashcards_due,
        featured=featured_item,
        recentActivity=[
            models.CourseActivityEntry(
                topicId=topic.id,
                topicTitle=topic.title,
                courseId=course_id,
                courseTitle=course_title,
                completedAt=topic.completed_at,
                estimatedHours=topic.estimated_hours,
            )
            for topic, course_id, course_title in recent
        ],
        timezoneKnown=summary["timezoneKnown"],
    )


@router.post("/courses", response_model=models.CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(body: models.CourseCreate, current_user: CurrentUser):
    """Create a course manually."""
    course = await course_service.create_course(
        user=current_user, data=body.model_dump(exclude_unset=True)
    )
    # snake_case ORM attributes, camelCase columns. See the note in `get_course`.
    return models.CourseResponse(
        id=course.id,
        userId=course.user_id,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        targetDate=course.target_date,
        isAIGenerated=course.is_ai_generated,
        archived=course.archived,
        progress=0.0,
        totalTopics=0,
        completedTopics=0,
        modules=[],
        createdAt=course.created_at,
        updatedAt=course.updated_at,
        category=course.category,
        tags=course.tags,
        outcomes=course.outcomes,
        sourcePrompt=course.source_prompt,
        teachingStyle=course.teaching_style,
        instructor=(
            models.CourseInstructor(name=course.instructor_name, role=course.instructor_role)
            if course.instructor_name
            else None
        ),
        # A course cannot have been rated in the request that created it, so this is the empty
        # aggregate rather than a query. `average` stays null, which is what "unrated" reads as.
        rating=models.CourseRatingSummary(average=None, count=0, yourRating=None),
    )


@router.get("/courses/{course_id}", response_model=models.CourseResponse)
async def get_course(course_id: str, current_user: CurrentUser):
    """Get course with all modules and topics."""
    course = await knowledge_repo.find_course_with_modules(course_id, current_user.id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    modules = []
    for m in course.modules or []:
        enriched = course_service.calculate_module_progress(m)
        modules.append(models.ModuleResponse(**enriched))

    progress, total_topics, completed_topics = await course_service.calculate_course_progress(
        course_id
    )
    outline_recorded = await knowledge_repo.has_outline_satisfaction(current_user.id, course_id)
    rating_average, rating_count, your_rating = await knowledge_repo.course_rating_summary(
        course_id, current_user.id
    )

    # The ORM attributes are snake_case even though the columns are camelCase — the
    # mapping is declared per column, e.g. `user_id: Mapped[str] = mapped_column("userId", ...)`.
    # Reading `course.userId` here raised `AttributeError`, so this route answered `500`
    # for every request. It went unnoticed because no client calls it: the web course
    # pages are still fixture-backed, so course detail has never been fetched.
    return models.CourseResponse(
        id=course.id,
        userId=course.user_id,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        targetDate=course.target_date,
        isAIGenerated=course.is_ai_generated,
        archived=course.archived,
        progress=progress,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        modules=modules,
        createdAt=course.created_at,
        updatedAt=course.updated_at,
        outlineSatisfactionRecorded=outline_recorded,
        category=course.category,
        tags=course.tags,
        outcomes=course.outcomes,
        sourcePrompt=course.source_prompt,
        teachingStyle=course.teaching_style,
        # The whole object is null when no name is stored, rather than an object with null fields. A
        # panel keyed on "is there an instructor" is a single check that way; the alternative asks
        # every reader to know that a nameless instructor means none.
        instructor=(
            models.CourseInstructor(name=course.instructor_name, role=course.instructor_role)
            if course.instructor_name
            else None
        ),
        rating=models.CourseRatingSummary(
            average=rating_average, count=rating_count, yourRating=your_rating
        ),
    )


@router.put("/courses/{course_id}", response_model=models.CourseResponse)
async def update_course(course_id: str, body: models.CourseUpdate, current_user: CurrentUser):
    """Update course metadata."""
    await course_service.update_course(
        course_id=course_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return await get_course(course_id, current_user)


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: str, current_user: CurrentUser):
    """Delete a course permanently (cascade)."""
    await course_service.delete_course(course_id=course_id, user_id=current_user.id)


@router.post("/courses/{course_id}/archive", response_model=models.CourseResponse)
async def archive_course(course_id: str, current_user: CurrentUser):
    """Archive a course."""
    await course_service.archive_course(course_id=course_id, user_id=current_user.id)
    return await get_course(course_id, current_user)


@router.post(
    "/courses/{course_id}/materials",
    response_model=models.ResourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_course_material(
    course_id: str,
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    """Attach a reference file to a course.

    Multipart, through the same storage service notes, generated documents and study-plan materials use,
    rather than a second upload mechanism. The create wizard's file drop kept filenames in browser memory
    and its own caption said so; this is where they go now.

    Stored as a `Resource` with a `courseId`, not in a new table — a resource already means "something
    worth reading, attached to a course", and the course page already lists them.
    """
    try:
        resource = await course_service.add_course_material(
            user_id=current_user.id, course_id=course_id, file=file
        )
    except ValueError as error:
        # A 502 rather than a 400: storage refused the object, the request itself was fine, and the
        # learner can do nothing differently.
        raise HTTPException(status_code=502, detail=str(error)) from error
    return resource


@router.post("/courses/{course_id}/unarchive", response_model=models.CourseResponse)
async def unarchive_course(course_id: str, current_user: CurrentUser):
    """Return an archived course to the library.

    A dedicated endpoint rather than documenting `PUT` with `archived: false`, for one reason:
    `POST .../archive` already exists, and an area where archiving is a named action while unarchiving is
    a field write invites each client to pick a different one. Two endpoints that mirror each other are
    easier to keep honest than one endpoint and one convention.

    `PUT` with `archived: false` does also work — clearing and setting fields through `PUT` was fixed
    along with the null-filtering defect — so this is the preferred path, not the only one.
    """
    await course_service.unarchive_course(course_id=course_id, user_id=current_user.id)
    return await get_course(course_id, current_user)


@router.post("/courses/{course_id}/outline-satisfaction", status_code=status.HTTP_201_CREATED)
async def record_outline_satisfaction(
    course_id: str, body: models.CourseOutlineSatisfactionCreate, current_user: CurrentUser
):
    """Record learner reaction to AI-generated outline (KPI)."""
    await course_service.check_course_ownership(course_id, current_user.id)
    await knowledge_repo.record_outline_satisfaction(
        {
            "userId": current_user.id,
            "courseId": course_id,
            "kind": body.kind,
            "feedback": body.feedback,
        }
    )
    return {"status": "ok"}


# ===========================================================================
# Modules
# ===========================================================================


@router.post("/courses/{course_id}/modules", response_model=models.ModuleResponse, status_code=201)
async def create_module(course_id: str, body: models.ModuleCreate, current_user: CurrentUser):
    """Add a module to a course."""
    await course_service.check_course_ownership(course_id, current_user.id)
    module = await knowledge_repo.create_module(
        {
            "courseId": course_id,
            "title": body.title,
            "order": body.order,
            "description": body.description,
        }
    )
    enriched = course_service.calculate_module_progress(module)
    return models.ModuleResponse(**enriched)


@router.put("/courses/{course_id}/modules/{module_id}", response_model=models.ModuleResponse)
async def update_module(
    course_id: str, module_id: str, body: models.ModuleUpdate, current_user: CurrentUser
):
    """Update a module."""
    module, _ = await course_service.check_module_ownership(module_id, current_user.id)
    if module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Module does not belong to this course")
    data = body.model_dump(exclude_unset=True)
    if data:
        await knowledge_repo.update_module(module_id, data)
    updated = await knowledge_repo.find_module_with_topics(module_id)
    return models.ModuleResponse(**course_service.calculate_module_progress(updated))


@router.delete("/courses/{course_id}/modules/{module_id}", status_code=204)
async def delete_module(course_id: str, module_id: str, current_user: CurrentUser):
    """Delete a module and its topics."""
    module, _ = await course_service.check_module_ownership(module_id, current_user.id)
    if module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Module does not belong to this course")
    await knowledge_repo.delete_module(module_id)
    # The topic set shrank, so the stored progress figure has to be recomputed or it keeps the
    # old denominator. Deleting the last incomplete topic makes a course 100% complete.
    await knowledge_repo.recount_course_progress(course_id)


# ===========================================================================
# Topics
# ===========================================================================


@router.post(
    "/courses/{course_id}/modules/{module_id}/topics",
    response_model=models.TopicResponse,
    status_code=201,
)
async def create_topic(
    course_id: str, module_id: str, body: models.TopicCreate, current_user: CurrentUser
):
    """Add a topic to a module."""
    module, _ = await course_service.check_module_ownership(module_id, current_user.id)
    if module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Module does not belong to this course")
    topic = await knowledge_repo.create_topic(
        {
            "moduleId": module_id,
            "title": body.title,
            "order": body.order,
            "content": body.content,
            "estimatedHours": body.estimatedHours,
        }
    )
    # Recounted because the topic set changed. `Course.progress` is stored, so adding or
    # removing a topic moves the denominator and the stored figure would otherwise be stale —
    # which is exactly how a derived-but-persisted column drifts.
    await knowledge_repo.recount_course_progress(course_id)
    return models.TopicResponse.model_validate(topic, from_attributes=True)


@router.post(
    "/courses/{course_id}/modules/{module_id}/topics/bulk",
    response_model=list[models.TopicResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_topics_bulk(
    course_id: str, module_id: str, body: models.TopicBulkCreate, current_user: CurrentUser
):
    """Save a module's whole outline in one request.

    The create wizard produces a dozen or more topics at once. Sent one at a time, a failure partway
    leaves half an outline that the learner then has to reconcile against what they typed — and they
    cannot see which half is missing. This writes all of them or none.

    Returns the created topics in the order they were written, because the caller renders the outline it
    just saved and should render what the server stored rather than what it hoped to store.
    """
    module, course = await course_service.check_module_ownership(module_id, current_user.id)
    if course.id != course_id:
        raise HTTPException(status_code=400, detail="Module path mismatch")

    topics = await knowledge_repo.create_topics(
        module_id, [topic.model_dump(exclude_unset=True) for topic in body.topics]
    )
    # A course's stored progress is derived from its topic set, so adding topics moves it. Recounted
    # here for the same reason single topic creation recounts: a stored derived value drifts the moment
    # the thing it derives from changes.
    await knowledge_repo.recount_course_progress(course_id)
    return [models.TopicResponse.model_validate(topic, from_attributes=True) for topic in topics]


@router.patch(
    "/courses/{course_id}/modules/reorder",
    response_model=models.ReorderResponse,
)
async def reorder_modules(course_id: str, body: models.ReorderRequest, current_user: CurrentUser):
    """Set module order from a sequence of ids, first to last.

    Ids rather than explicit order numbers: the caller knows the sequence it wants, not the float values
    that encode it, and letting a client send the numbers means a dragged item can be given an order
    that collides with another's.

    Ids that do not belong to this course move nothing — the update carries the course id — and the
    returned count is what actually moved, so a caller that sent something stale can tell.
    """
    await course_service.check_course_ownership(course_id, current_user.id)
    moved = await knowledge_repo.reorder_modules(course_id, body.ids)
    return models.ReorderResponse(reordered=moved)


@router.patch(
    "/courses/{course_id}/modules/{module_id}/topics/reorder",
    response_model=models.ReorderResponse,
)
async def reorder_topics(
    course_id: str, module_id: str, body: models.ReorderRequest, current_user: CurrentUser
):
    """Set topic order within a module. Same contract as module reorder."""
    module, course = await course_service.check_module_ownership(module_id, current_user.id)
    if course.id != course_id:
        raise HTTPException(status_code=400, detail="Module path mismatch")

    moved = await knowledge_repo.reorder_topics(module_id, body.ids)
    return models.ReorderResponse(reordered=moved)


@router.put(
    "/courses/{course_id}/modules/{module_id}/topics/{topic_id}",
    response_model=models.TopicResponse,
)
async def update_topic(
    course_id: str,
    module_id: str,
    topic_id: str,
    body: models.TopicUpdate,
    current_user: CurrentUser,
):
    """Update a topic."""
    topic, module, _ = await course_service.check_topic_ownership(topic_id, current_user.id)
    if topic.module_id != module_id or module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Topic path mismatch")
    data = body.model_dump(exclude_unset=True)
    if not data:
        return models.TopicResponse.model_validate(topic, from_attributes=True)
    updated = await knowledge_repo.update_topic(topic_id, data)

    # Emit completion events if status changed
    if body.completed is not None:
        if body.completed:
            from ..events import emit_topic_completed

            await emit_topic_completed(current_user.id, topic_id, course_id)
        else:
            from ..events import emit_topic_uncompleted

            await emit_topic_uncompleted(current_user.id, topic_id, course_id)

    return models.TopicResponse.model_validate(updated, from_attributes=True)


@router.delete("/courses/{course_id}/modules/{module_id}/topics/{topic_id}", status_code=204)
async def delete_topic(course_id: str, module_id: str, topic_id: str, current_user: CurrentUser):
    """Delete a topic."""
    topic, module, _ = await course_service.check_topic_ownership(topic_id, current_user.id)
    if topic.module_id != module_id or module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Topic path mismatch")
    await knowledge_repo.delete_topic(topic_id)
    # The topic set shrank, so the stored progress figure has to be recomputed or it keeps the
    # old denominator. Deleting the last incomplete topic makes a course 100% complete.
    await knowledge_repo.recount_course_progress(course_id)


@router.patch(
    "/courses/{course_id}/modules/{module_id}/topics/{topic_id}/complete",
    response_model=models.TopicResponse,
)
async def toggle_topic_completion(
    course_id: str,
    module_id: str,
    topic_id: str,
    current_user: CurrentUser,
    completed: bool = Query(...),
):
    """Mark/unmark a topic as completed."""
    updated = await course_service.toggle_topic_completion(
        topic_id=topic_id,
        module_id=module_id,
        course_id=course_id,
        user_id=current_user.id,
        completed=completed,
    )
    return models.TopicResponse.model_validate(updated, from_attributes=True)


@router.post(
    "/courses/{course_id}/modules/{module_id}/topics/{topic_id}/generate",
    response_model=models.TopicGenerateResponse,
)
async def generate_topic_content(
    course_id: str,
    module_id: str,
    topic_id: str,
    body: models.TopicGenerateRequest,
    current_user: CurrentUser,
):
    """Generate learning content for a topic, and keep what is worth keeping.

    This endpoint used to compose a prompt, receive content, return it and store nothing — so a
    learner read a generated lesson once, and reopening the topic produced a different one or an empty
    page. Every type now either persists its result or states that it did not.

    - `explain` writes the lesson: `content`, `objectives`, `knowledgeCheck` and the sections. It is
      the only type that *is* the topic's body, so it replaces it.
    - `flashcards` creates real `Flashcard` rows through the flashcard service, where cards live and
      where SM-2 picks them up for review. It previously returned markdown no deck ever saw.
    - `quiz` and `summary` are returned and not stored, deliberately. A summary written into `content`
      would replace the lesson with a condensation of it, and a five-question quiz is not the single
      check a topic holds. Scored, recorded quizzes belong to the preparation domain.

    `persisted` reports what happened, rather than leaving the caller to infer it from the type.
    """
    topic, module, _ = await course_service.check_topic_ownership(topic_id, current_user.id)
    if topic.module_id != module_id or module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Topic path mismatch")

    if body.type == "flashcards":
        if not body.deckId:
            raise HTTPException(
                status_code=422,
                detail="deckId is required for flashcard generation, so the cards land in a deck.",
            )
        from src.domains.personal_learning.services import flashcard_service

        cards = await flashcard_service.generate_from_topic(
            user_id=current_user.id, topic_id=topic_id, deck_id=body.deckId
        )
        if not cards:
            raise HTTPException(status_code=502, detail="No flashcards could be generated")
        return models.TopicGenerateResponse(
            type=body.type,
            topicId=topic_id,
            # The cards are the artifact. This is a receipt of what was filed, so the caller can show
            # the result without a second request to the deck.
            content="\n\n".join(f"**{card.front}**\n\n{card.back}" for card in cards),
            persisted=f"{len(cards)} flashcards",
        )

    if body.type == "explain":
        from src.domains.personal_learning.services.llm_resilient import generate_content_json

        payload = await generate_content_json(
            lesson_service.build_lesson_prompt(topic.title, topic.content),
            max_tokens=4096,
            fallback=None,
            user_id=current_user.id,
        )
        parsed = lesson_service.parse_lesson(payload)
        if not parsed["sections"]:
            # Nothing usable came back. Failing is better than persisting an empty lesson over the
            # topic's existing content, which would destroy a working body to store nothing.
            raise HTTPException(
                status_code=502, detail="The generated lesson could not be read. Please try again."
            )

        markdown = lesson_service.render_markdown(topic.title, parsed)
        written = await lesson_service.persist_lesson(topic_id, markdown=markdown, parsed=parsed)
        return models.TopicGenerateResponse(
            type=body.type, topicId=topic_id, content=markdown, persisted=f"{written} sections"
        )

    from src.domains.intelligence.reasoning.llm import generate_content

    prompts = {
        "quiz": f'Create a 5-question quiz on "{topic.title}" with answers. Markdown.',
        "summary": f'Summarize "{topic.title}" with key points and takeaways. Bullet points.',
    }
    prompt = prompts[body.type]
    if topic.content:
        prompt += f"\n\nExisting context:\n{topic.content[:2000]}"

    content = await generate_content(prompt)
    if not content:
        raise HTTPException(status_code=500, detail="AI returned empty content")

    return models.TopicGenerateResponse(type=body.type, topicId=topic_id, content=content)


# ===========================================================================
# Resources
# ===========================================================================


@router.get("/resources", response_model=models.ResourceListResponse)
async def list_resources(
    current_user: CurrentUser,
    circle_id: str | None = Query(None, alias="space_id"),
    topicId: str | None = Query(None),
    courseId: str | None = Query(None),
    type: str | None = Query(None),
    search: str | None = Query(None, max_length=255),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sortBy: str = Query("createdAt"),
    sortOrder: str = Query("desc"),
):
    """List resources with filters and pagination."""
    result = await resource_service.list_resources(
        user_id=current_user.id,
        space_id=circle_id,
        topic_id=topicId,
        course_id=courseId,
        resource_type=type,
        search=search,
        page=page,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return result


@router.post("/resources")
async def create_resource(body: models.ResourceCreate, current_user: CurrentUser):
    """Create a new resource."""
    return await resource_service.create_resource(
        user=current_user, data=body.model_dump(exclude_unset=True)
    )


@router.post("/resources/recommend", response_model=models.ResourceRecommendationResponse)
async def recommend_resources(
    body: models.ResourceRecommendationRequest, current_user: CurrentUser
):
    """Get AI-recommended resources via RAG."""
    result = await resource_service.recommend_resources(
        user_id=current_user.id, query=body.query, limit=body.limit, context=body.context
    )
    return result


@router.post("/resources/{resource_id}/interact")
async def record_interaction(resource_id: str, interaction_type: str, current_user: CurrentUser):
    """Record a user interaction with a resource."""
    await resource_service.record_interaction(
        user_id=current_user.id, resource_id=resource_id, interaction_type=interaction_type
    )
    return {"success": True, "message": "Interaction recorded"}


@router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: str, current_user: CurrentUser):
    """Delete a resource."""
    await resource_service.delete_resource(user_id=current_user.id, resource_id=resource_id)
    return {"success": True, "message": "Resource deleted"}
