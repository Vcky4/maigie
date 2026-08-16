"""
Knowledge domain — API routes.

Endpoints for courses (CRUD, progress, AI generation),
modules, topics, and resources.

Mounted at: /api/v1/knowledge
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from src.shared.auth import CurrentUser

from . import models
from .repository import knowledge_repo
from .services import course_service, resource_service

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

    # One grouped query for the whole page, rather than a progress call per course that issued its
    # own module query underneath. Courses with no modules are absent from the map and read as zeros.
    totals = await knowledge_repo.course_progress_totals([c.id for c in courses])

    items = []
    for c in courses:
        module_count, total_topics, completed_topics = totals.get(c.id, (0, 0, 0))
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
                createdAt=c.created_at,
                updatedAt=c.updated_at,
            )
        )

    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.CourseListResponse(
        items=items, total=total, page=page, pageSize=pageSize, pages=pages
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
    return models.TopicResponse.model_validate(topic, from_attributes=True)


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
    """Generate AI learning content for a topic (explain, quiz, summary, flashcards)."""
    topic, module, _ = await course_service.check_topic_ownership(topic_id, current_user.id)
    if topic.module_id != module_id or module.course_id != course_id:
        raise HTTPException(status_code=400, detail="Topic path mismatch")

    # Delegate to Intelligence layer (LLM)
    from src.domains.intelligence.reasoning.llm import generate_content

    prompts = {
        "explain": f'Explain "{topic.title}" clearly with examples. Markdown format.',
        "quiz": f'Create a 5-question quiz on "{topic.title}" with answers. Markdown.',
        "summary": f'Summarize "{topic.title}" with key points and takeaways. Bullet points.',
        "flashcards": f'Create 5-7 Q&A flashcards about "{topic.title}". Markdown.',
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
