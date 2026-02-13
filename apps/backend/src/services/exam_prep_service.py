"""
Exam Prep service: CRUD, study plan generation, and quiz from materials.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from prisma import Prisma


def _distribute_study_blocks(
    exam_date: datetime, num_blocks: int = 10, block_minutes: int = 60
) -> list[tuple[datetime, datetime]]:
    """Distribute study blocks from now until exam date."""
    now = datetime.now(UTC)
    if exam_date.tzinfo is None:
        exam_date = exam_date.replace(tzinfo=UTC)
    if exam_date <= now:
        return []

    days_until = (exam_date - now).days
    if days_until < 1:
        return []

    # Spread blocks across available days
    blocks: list[tuple[datetime, datetime]] = []
    for i in range(min(num_blocks, days_until)):
        day_offset = (i * days_until) // num_blocks if num_blocks > 0 else i
        start = (now + timedelta(days=day_offset)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        if start < now:
            start = now + timedelta(hours=1)
        end = start + timedelta(minutes=block_minutes)
        if end <= exam_date:
            blocks.append((start, end))
    return blocks


async def create_exam_prep(
    db: Prisma,
    user_id: str,
    subject: str,
    exam_date: datetime,
    description: str | None = None,
) -> Any:
    """Create an ExamPrep and optionally generate initial study schedule."""
    exam_prep = await db.examprep.create(
        data={
            "userId": user_id,
            "subject": subject,
            "examDate": exam_date,
            "description": description,
        }
    )
    # Generate study blocks
    blocks = _distribute_study_blocks(exam_date)
    for start_at, end_at in blocks:
        await db.scheduleblock.create(
            data={
                "userId": user_id,
                "title": f"Exam Prep: {subject}",
                "description": "Study session for exam preparation",
                "startAt": start_at,
                "endAt": end_at,
                "examPrepId": exam_prep.id,
            }
        )
    return exam_prep


async def add_material(
    db: Prisma,
    exam_prep_id: str,
    user_id: str,
    filename: str,
    url: str,
    extracted_text: str | None = None,
    file_type: str | None = None,
    size: int | None = None,
) -> Any:
    """Add a material to an exam prep."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")
    return await db.examprepmaterial.create(
        data={
            "examPrepId": exam_prep_id,
            "filename": filename,
            "url": url,
            "extractedText": extracted_text,
            "fileType": file_type,
            "size": size,
        }
    )


async def generate_study_plan(db: Prisma, exam_prep_id: str, user_id: str) -> int:
    """Regenerate study blocks for exam prep. Returns count of blocks created."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    # Remove existing exam prep blocks
    await db.scheduleblock.update_many(
        where={"examPrepId": exam_prep_id},
        data={"examPrepId": None},
    )

    blocks = _distribute_study_blocks(exam_prep.examDate)
    for start_at, end_at in blocks:
        await db.scheduleblock.create(
            data={
                "userId": user_id,
                "title": f"Exam Prep: {exam_prep.subject}",
                "description": "Study session for exam preparation",
                "startAt": start_at,
                "endAt": end_at,
                "examPrepId": exam_prep.id,
            }
        )
    return len(blocks)
