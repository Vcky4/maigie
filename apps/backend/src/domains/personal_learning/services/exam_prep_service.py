"""
Exam Prep service — full preparation lifecycle.

Manages preparations for exams, certifications, interviews, presentations,
assignments, and projects. AI extracts topics from uploaded materials and
generates day-by-day study plans.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def create_preparation(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Create a preparation record with status "SETUP".

    Req 4.1: Store subject, type (EXAM, CERTIFICATION, INTERVIEW, etc.), target date.
    """
    # Accept both the field name and the wire alias.
    target_date = data.get("target_date") or data.get("targetDate")
    if isinstance(target_date, str):
        target_date = datetime.fromisoformat(target_date.replace("Z", "+00:00"))

    prep_data = {
        "userId": user_id,
        "subject": data["subject"],
        # Persisted since migration 006. Previously accepted and discarded.
        "type": data.get("prep_type") or data.get("type"),
        "examDate": target_date,
        "description": data.get("description"),
        "status": "SETUP",
        # Persisted since migration 007. The wizard collected both and dropped them.
        "confidence": data.get("confidence"),
        "pace": data.get("pace"),
    }
    prep = await repo.create_exam_prep(prep_data)

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="preparation_created",
        title=f"Started preparation: {data['subject']}",
        context={"source": "personal", "prepId": prep.id, "type": prep_data["type"]},
    )

    return prep


async def get_preparation(*, user_id: str, prep_id: str) -> Any:
    """Get a preparation by ID. Raises NotFoundError if not found."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return prep


# `list_preparations` was removed. It fetched every preparation and sorted in
# Python; `search_preparations` below does the same ordering in SQL with
# filtering and pagination, and is what the route uses.


async def search_preparations(
    *,
    user_id: str,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """Filtered, paginated preparations ordered by target date."""
    return await repo.search_exam_preps(
        user_id,
        status=status,
        search=search,
        skip=(page - 1) * page_size,
        take=page_size,
    )


async def update_preparation(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """Update preparation fields.

    Translates the request's field names onto the repository's wire names and
    parses the target date, so the route does not have to.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    mapped: dict[str, Any] = {}
    if "subject" in data:
        mapped["subject"] = data["subject"]
    if "description" in data:
        mapped["description"] = data["description"]
    if "status" in data:
        mapped["status"] = data["status"]
    if "prep_type" in data:
        mapped["type"] = data["prep_type"]
    for intent_field in ("confidence", "pace"):
        if intent_field in data:
            mapped[intent_field] = data[intent_field]
    for key in ("target_date", "exam_date"):
        if key in data and data[key] is not None:
            value = data[key]
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            mapped["examDate"] = value

    if not mapped:
        return prep
    return await repo.update_exam_prep(prep_id, mapped)


async def delete_preparation(*, user_id: str, prep_id: str) -> bool:
    """Delete a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        return False
    await repo.delete_exam_prep(prep_id)
    return True


async def upload_material(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """
    Upload material to a preparation.

    Req 4.2: Store material and trigger AI extraction of key topics.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material_data = {
        "prepId": prep_id,
        "filename": data["filename"],
        "url": data["url"],
        "fileType": data.get("fileType"),
        "size": data.get("size"),
        "extractedText": data.get("extractedText"),
        "category": data.get("category", "OTHER"),
        "label": data.get("label"),
    }

    material = await repo.create_prep_material(material_data)

    # Update preparation status to IN_PROGRESS if still in SETUP
    if prep.status == "SETUP":
        await repo.update_exam_prep(prep_id, {"status": "IN_PROGRESS"})

    return material


async def list_materials(*, user_id: str, prep_id: str) -> list[dict[str, Any]]:
    """List materials for a preparation.

    Returns listing shapes that omit `extractedText`: it can hold an entire
    chapter per row and is not needed to render a material list.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    materials = await repo.list_prep_materials(prep_id)
    return [
        {
            "id": material.id,
            "prepId": material.prep_id,
            "filename": material.filename,
            "url": material.url,
            "fileType": material.file_type,
            "size": material.size,
            "category": material.category,
            "label": material.label,
            "hasExtractedText": bool(material.extracted_text),
            "createdAt": material.created_at,
        }
        for material in materials
    ]


async def update_material(
    *, user_id: str, prep_id: str, material_id: str, data: dict[str, Any]
) -> Any:
    """Update a material's category or label."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material = await repo.find_prep_material(material_id, prep_id)
    if not material:
        raise NotFoundError("PrepMaterial", material_id)

    payload = {key: value for key, value in data.items() if key in ("category", "label")}
    if not payload:
        return material
    return await repo.update_prep_material(material_id, payload)


async def delete_material(*, user_id: str, prep_id: str, material_id: str) -> bool:
    """Delete a material from a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material = await repo.find_prep_material(material_id, prep_id)
    if not material:
        return False
    await repo.delete_prep_material(material_id)
    return True


async def update_topic(*, user_id: str, prep_id: str, topic_id: str, data: dict[str, Any]) -> Any:
    """Update a topic belonging to a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    topic = await repo.find_prep_topic(topic_id, prep_id)
    if not topic:
        raise NotFoundError("PrepTopic", topic_id)

    field_map = {
        "title": "title",
        "description": "description",
        "estimated_minutes": "estimatedMinutes",
        "order_index": "orderIndex",
        "mastery_score": "masteryScore",
        "status": "status",
    }
    payload = {field_map[k]: v for k, v in data.items() if k in field_map}
    if not payload:
        return topic
    return await repo.update_prep_topic(topic_id, payload)


async def delete_topic(*, user_id: str, prep_id: str, topic_id: str) -> bool:
    """Delete a topic from a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    topic = await repo.find_prep_topic(topic_id, prep_id)
    if not topic:
        return False
    await repo.delete_prep_topic(topic_id)
    return True


async def extract_topics(*, user_id: str, prep_id: str) -> list[Any]:
    """
    AI-extract key topics from preparation materials.

    Req 4.3: Create topic records with titles, descriptions, and estimated study time.
    """
    from src.domains.intelligence.reasoning.llm import generate_content
    import json

    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    # Gather material text
    materials = await repo.list_prep_materials(prep_id)
    material_text = "\n\n".join(
        [f"[{m.filename}]: {m.extracted_text or ''}" for m in materials if m.extracted_text]
    )

    if not material_text:
        material_text = f"Subject: {prep.subject}\nDescription: {prep.description or ''}"

    prompt = (
        f"Analyze this learning material and extract the key topics for study.\n"
        f"Subject: {prep.subject}\n"
        f"Materials:\n{material_text[:5000]}\n\n"
        f"Return a JSON array of topic objects with:\n"
        f"- 'title': short topic name\n"
        f"- 'description': brief description of what to learn\n"
        f"- 'estimatedMinutes': estimated study time in minutes (15-120)\n\n"
        f"Generate 5-15 topics covering all important areas.\n"
        f"Return ONLY the JSON array."
    )

    try:
        response = await generate_content(prompt, max_tokens=3000)
        topics_data = json.loads(response)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Failed to extract topics for prep {prep_id}: {e}")
        return []

    created_topics = []
    for idx, topic in enumerate(topics_data):
        if isinstance(topic, dict) and "title" in topic:
            prep_topic = await repo.create_prep_topic(
                {
                    "prepId": prep_id,
                    "title": topic["title"],
                    "description": topic.get("description"),
                    "estimatedMinutes": topic.get("estimatedMinutes", 30),
                    "orderIndex": idx,
                    "status": "NOT_STARTED",
                }
            )
            created_topics.append(prep_topic)

    return created_topics


async def list_topics(*, user_id: str, prep_id: str) -> list[Any]:
    """List topics for a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return await repo.list_prep_topics(prep_id)


async def mark_completed(*, user_id: str, prep_id: str) -> Any:
    """
    Mark a preparation as completed.

    Req 4.10: Preserve all data for review.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    result = await repo.update_exam_prep(prep_id, {"status": "COMPLETED"})

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="preparation_completed",
        title=f"Completed preparation: {prep.subject}",
        context={"source": "personal", "prepId": prep_id},
    )

    # Check milestones (first_prep_complete)
    from . import milestone_service

    completed_preps = await repo.list_exam_preps(user_id)
    preps_completed = len([p for p in completed_preps if p.status == "COMPLETED"])
    await milestone_service.check_milestones(user_id, {"preps_completed": preps_completed})

    return result


async def mark_overdue_preparations_completed() -> int:
    """
    Background task: mark preparations past target date as completed.
    Called by Celery beat daily. Returns count of updated preparations.
    """
    from sqlalchemy import select as sa_select
    from src.domains.personal_learning.db_models import ExamPrep
    from src.shared.database import get_session_factory

    now = datetime.now(timezone.utc)
    factory = get_session_factory()

    async with factory() as session:
        stmt = sa_select(ExamPrep).where(
            ExamPrep.exam_date < now,
            ExamPrep.status != "COMPLETED",
        )
        result = await session.execute(stmt)
        overdue_preps = list(result.scalars().all())

    count = 0
    for prep in overdue_preps:
        try:
            await repo.update_exam_prep(prep.id, {"status": "COMPLETED"})
            count += 1
        except Exception as e:
            logger.error(f"Failed to mark prep {prep.id} as completed: {e}")

    return count


async def search_question_bank(
    *,
    user_id: str,
    prep_id: str,
    topic_id: str | None = None,
    difficulty: str | None = None,
    source: str | None = None,
    flagged_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """A page of a preparation's question bank.

    Returns listing shapes that **omit the answer key**. The bank is a browsing
    surface, so including `correctAnswer` would let a learner read every answer
    without practising — reopening the leak Decision C closed at quiz start.

    Ownership is checked on the preparation, and the query is scoped to it, so a
    topic or question id from another learner's preparation cannot be reached.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    rows, total = await repo.search_prep_questions(
        prep_id,
        user_id=user_id,
        topic_id=topic_id,
        difficulty=difficulty,
        source=source,
        flagged_only=flagged_only,
        skip=(page - 1) * page_size,
        take=page_size,
    )

    items = [
        {
            "id": question.id,
            "prepId": question.prep_id,
            "prepTopicId": question.prep_topic_id,
            "questionText": question.question_text,
            "questionType": question.question_type,
            "options": question.options if isinstance(question.options, list) else None,
            "difficulty": question.difficulty,
            "source": question.source,
            "sourceYear": question.source_year,
            "timesAnswered": question.times_answered or 0,
            "timesCorrect": question.times_correct or 0,
            # None rather than 0 until attempted, so an unpractised question does
            # not read as one the learner always gets wrong.
            "accuracyPercent": (
                round((question.times_correct / question.times_answered) * 100, 1)
                if question.times_answered
                else None
            ),
            "isFlagged": flag is not None,
            "flagNote": flag.note if flag is not None else None,
            "createdAt": question.created_at,
        }
        for question, flag in rows
    ]
    return items, total


async def flag_question(
    *, user_id: str, prep_id: str, question_id: str, note: str | None = None
) -> dict[str, Any]:
    """Flag a banked question for later review.

    Idempotent: flagging an already-flagged question updates the note if one is
    supplied and otherwise changes nothing, so a repeated tap is not an error.

    Scoped twice, as elsewhere: the preparation must belong to the learner, and the
    question must belong to that preparation.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    question = await repo.find_prep_question(question_id, prep_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    flag = await repo.upsert_question_flag(user_id=user_id, prep_question_id=question_id, note=note)
    return {
        "questionId": question_id,
        "isFlagged": True,
        "note": flag.note,
        "createdAt": flag.created_at,
    }


async def unflag_question(*, user_id: str, prep_id: str, question_id: str) -> None:
    """Remove a learner's flag from a question.

    Succeeds whether or not a flag was present. Unflagging something already
    unflagged is the outcome the caller wanted, not an error.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    question = await repo.find_prep_question(question_id, prep_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    await repo.delete_question_flag(user_id=user_id, prep_question_id=question_id)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

# The exam itself is always the last thing on the timeline, whatever the plan says.
_EXAM_MILESTONE_KIND = "EXAM"
_STUDY_MILESTONE_KIND = "STUDY"


async def get_timeline(*, user_id: str, prep_id: str) -> dict[str, Any]:
    """A preparation's timeline, derived from its linked study plan.

    **No milestone entity.** A preparation can already generate a study plan whose
    items carry a scheduled date, an estimate, a status, and a topic — which is a
    timeline. Adding a parallel `PrepMilestone` table would create a second answer
    to "what should I do by when", and the two would drift the first time a plan was
    regenerated or an item rescheduled.

    The target date is appended as a final milestone, because it is the one date the
    learner is actually working towards and it belongs on the same axis.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    plans = await repo.list_prep_study_plans(prep_id, user_id)

    milestones: list[dict[str, Any]] = []
    for plan in plans:
        for item in plan.items or []:
            milestones.append(
                {
                    "id": item.id,
                    "kind": _STUDY_MILESTONE_KIND,
                    "title": item.title,
                    "detail": item.description,
                    "scheduledFor": item.scheduled_date,
                    "estimatedMinutes": item.estimated_minutes,
                    "status": item.status,
                    "itemType": item.item_type,
                    "prepTopicId": item.prep_topic_id,
                    "studyPlanId": plan.id,
                    "completedAt": item.completed_at,
                }
            )

    milestones.sort(key=lambda milestone: milestone["scheduledFor"])

    # The exam is a milestone too, and the only one guaranteed to exist.
    milestones.append(
        {
            "id": f"exam-{prep.id}",
            "kind": _EXAM_MILESTONE_KIND,
            "title": prep.subject,
            "detail": None,
            "scheduledFor": prep.exam_date,
            "estimatedMinutes": None,
            "status": prep.status,
            "itemType": None,
            "prepTopicId": None,
            "studyPlanId": None,
            "completedAt": None,
        }
    )

    return {
        "preparationId": prep_id,
        # False when no plan has been generated yet. The client uses this to offer
        # plan generation instead of rendering a timeline containing only the exam,
        # which would read as though the work had been planned and found to be empty.
        "hasStudyPlan": bool(plans),
        "milestones": milestones,
    }
