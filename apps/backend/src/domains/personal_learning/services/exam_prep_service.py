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
    prep_data = {
        "userId": user_id,
        "subject": data["subject"],
        "examDate": data["targetDate"],  # Maps to exam_date column
        "description": data.get("description"),
        "status": "SETUP",
    }
    return await repo.create_exam_prep(prep_data)


async def get_preparation(*, user_id: str, prep_id: str) -> Any:
    """Get a preparation by ID. Raises NotFoundError if not found."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return prep


async def list_preparations(*, user_id: str) -> list[Any]:
    """
    List all preparations sorted by target date with status and progress.

    Req 4.11: Return sorted by target date with progress percentage and days remaining.
    """
    preps = await repo.list_exam_preps(user_id)
    # Sort by exam_date (target date)
    preps.sort(
        key=lambda p: p.exam_date if p.exam_date else datetime.max.replace(tzinfo=timezone.utc)
    )
    return preps


async def update_preparation(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """Update preparation fields."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return await repo.update_exam_prep(prep_id, data)


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


async def list_materials(*, user_id: str, prep_id: str) -> list[Any]:
    """List materials for a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return await repo.list_prep_materials(prep_id)


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
    return await repo.update_exam_prep(prep_id, {"status": "COMPLETED"})


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
