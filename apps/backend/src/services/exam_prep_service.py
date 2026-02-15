"""
Exam Prep service: CRUD, study plan generation, material management, and status lifecycle.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from prisma import Prisma

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Study-plan distribution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_exam_prep(
    db: Prisma,
    user_id: str,
    subject: str,
    exam_date: datetime,
    description: str | None = None,
) -> Any:
    """Create an ExamPrep in SETUP status."""
    exam_prep = await db.examprep.create(
        data={
            "userId": user_id,
            "subject": subject,
            "examDate": exam_date,
            "description": description,
            "status": "SETUP",
        }
    )
    return exam_prep


async def update_exam_prep(
    db: Prisma,
    exam_prep_id: str,
    user_id: str,
    data: dict,
) -> Any:
    """Update exam prep fields. Only owner can update."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    update_data = {}
    for key in ("subject", "description", "status"):
        if key in data and data[key] is not None:
            update_data[key] = data[key]
    if "examDate" in data and data["examDate"] is not None:
        update_data["examDate"] = data["examDate"]

    if not update_data:
        return exam_prep

    return await db.examprep.update(
        where={"id": exam_prep_id},
        data=update_data,
    )


async def transition_status(
    db: Prisma,
    exam_prep_id: str,
    user_id: str,
    new_status: str,
) -> Any:
    """Transition exam prep to a new status."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    return await db.examprep.update(
        where={"id": exam_prep_id},
        data={"status": new_status},
    )


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


async def add_material(
    db: Prisma,
    exam_prep_id: str,
    user_id: str,
    filename: str,
    url: str,
    category: str = "OTHER",
    label: str | None = None,
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
            "category": category,
            "label": label,
        }
    )


async def update_material(
    db: Prisma,
    material_id: str,
    exam_prep_id: str,
    user_id: str,
    data: dict,
) -> Any:
    """Update material category/label."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    material = await db.examprepmaterial.find_first(
        where={"id": material_id, "examPrepId": exam_prep_id}
    )
    if not material:
        raise ValueError("Material not found")

    update_data = {}
    for key in ("category", "label"):
        if key in data and data[key] is not None:
            update_data[key] = data[key]

    if not update_data:
        return material

    return await db.examprepmaterial.update(
        where={"id": material_id},
        data=update_data,
    )


async def delete_material(
    db: Prisma,
    material_id: str,
    exam_prep_id: str,
    user_id: str,
) -> None:
    """Delete a material from an exam prep."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    material = await db.examprepmaterial.find_first(
        where={"id": material_id, "examPrepId": exam_prep_id}
    )
    if not material:
        raise ValueError("Material not found")

    await db.examprepmaterial.delete(where={"id": material_id})


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


async def save_topics(
    db: Prisma,
    exam_prep_id: str,
    topics: list[dict],
) -> list[Any]:
    """Save AI-extracted topics for an exam prep. Replaces existing topics."""
    # Delete existing topics (cascades to questions via ExamPrepTopic)
    await db.exampreptopic.delete_many(where={"examPrepId": exam_prep_id})

    created = []
    for i, topic_data in enumerate(topics):
        topic = await db.exampreptopic.create(
            data={
                "examPrepId": exam_prep_id,
                "title": topic_data["title"],
                "description": topic_data.get("description"),
                "order": i,
            }
        )
        created.append(topic)
    return created


async def update_topic(
    db: Prisma,
    topic_id: str,
    exam_prep_id: str,
    user_id: str,
    data: dict,
) -> Any:
    """Update an exam prep topic."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    topic = await db.exampreptopic.find_first(where={"id": topic_id, "examPrepId": exam_prep_id})
    if not topic:
        raise ValueError("Topic not found")

    update_data = {}
    for key in ("title", "description"):
        if key in data and data[key] is not None:
            update_data[key] = data[key]

    return await db.exampreptopic.update(
        where={"id": topic_id},
        data=update_data,
    )


async def delete_topic(
    db: Prisma,
    topic_id: str,
    exam_prep_id: str,
    user_id: str,
) -> None:
    """Delete an exam prep topic (cascades to questions)."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    topic = await db.exampreptopic.find_first(where={"id": topic_id, "examPrepId": exam_prep_id})
    if not topic:
        raise ValueError("Topic not found")

    await db.exampreptopic.delete(where={"id": topic_id})


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


async def save_questions(
    db: Prisma,
    topic_id: str,
    questions: list[dict],
) -> list[Any]:
    """Save questions for a topic. Appends to existing questions."""
    from prisma import Json

    created = []
    for q in questions:
        # Build create data — use relation connect for required topic,
        # and prisma Json wrapper for the nullable Json field.
        options_val = q.get("options")
        data: dict[str, Any] = {
            "topic": {"connect": {"id": topic_id}},
            "source": q.get("source", "AI_GENERATED"),
            "questionText": q["questionText"],
            "questionType": q.get("questionType", "MULTIPLE_CHOICE"),
            "correctAnswer": q.get("correctAnswer"),
            "explanation": q.get("explanation", ""),
            "year": q.get("year"),
            "difficulty": q.get("difficulty", "MEDIUM"),
            "tags": q.get("tags", []),
        }

        # Prisma Json fields need explicit Json() wrapper, not raw Python dicts/None
        if options_val is not None:
            data["options"] = Json(options_val)

        # Optional material relation
        material_id = q.get("materialId")
        if material_id:
            data["material"] = {"connect": {"id": material_id}}

        question = await db.examquestion.create(data=data)
        created.append(question)
    return created


# ---------------------------------------------------------------------------
# Study plan
# ---------------------------------------------------------------------------


async def generate_study_plan(db: Prisma, exam_prep_id: str, user_id: str) -> int:
    """Regenerate study blocks for exam prep. Returns count of blocks created."""
    exam_prep = await db.examprep.find_first(
        where={"id": exam_prep_id, "userId": user_id},
        include={"topics": True},
    )
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    # Remove existing exam prep blocks
    await db.scheduleblock.update_many(
        where={"examPrepId": exam_prep_id},
        data={"examPrepId": None},
    )

    # Determine number of blocks based on topics count
    num_topics = len(exam_prep.topics) if exam_prep.topics else 5
    num_blocks = max(num_topics, 10)

    blocks = _distribute_study_blocks(exam_prep.examDate, num_blocks=num_blocks)
    for i, (start_at, end_at) in enumerate(blocks):
        # Try to assign each block to a topic
        topic_title = ""
        if exam_prep.topics and i < len(exam_prep.topics):
            topic_title = f" - {exam_prep.topics[i].title}"

        await db.scheduleblock.create(
            data={
                "userId": user_id,
                "title": f"Exam Prep: {exam_prep.subject}{topic_title}",
                "description": "Study session for exam preparation",
                "startAt": start_at,
                "endAt": end_at,
                "examPrepId": exam_prep.id,
            }
        )
    return len(blocks)


# ---------------------------------------------------------------------------
# Progress & Analytics helpers
# ---------------------------------------------------------------------------


async def get_exam_prep_progress(db: Prisma, exam_prep_id: str, user_id: str) -> dict:
    """Calculate overall progress and readiness for an exam prep."""
    exam_prep = await db.examprep.find_first(
        where={"id": exam_prep_id, "userId": user_id},
        include={
            "topics": {"include": {"questions": {"include": {"attempts": True}}}},
            "quizSessions": {"where": {"completedAt": {"not": None}}},
        },
    )
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    total_questions = 0
    mastered_questions = 0
    learning_questions = 0
    new_questions = 0
    topic_progress = []

    for topic in exam_prep.topics or []:
        topic_total = len(topic.questions)
        topic_mastered = 0
        topic_learning = 0
        topic_new = 0

        for question in topic.questions:
            total_questions += 1
            mastery = _calculate_question_mastery(question.attempts)
            if mastery == "MASTERED":
                mastered_questions += 1
                topic_mastered += 1
            elif mastery in ("LEARNING", "FAMILIAR"):
                learning_questions += 1
                topic_learning += 1
            else:
                new_questions += 1
                topic_new += 1

        topic_mastery_pct = (topic_mastered / topic_total * 100) if topic_total > 0 else 0
        topic_progress.append(
            {
                "topicId": topic.id,
                "title": topic.title,
                "totalQuestions": topic_total,
                "mastered": topic_mastered,
                "learning": topic_learning,
                "new": topic_new,
                "masteryPercentage": round(topic_mastery_pct, 1),
            }
        )

    # Overall readiness score
    readiness = (mastered_questions / total_questions * 100) if total_questions > 0 else 0

    # Quiz history stats
    completed_quizzes = exam_prep.quizSessions or []
    avg_score = 0.0
    if completed_quizzes:
        scores = [q.score for q in completed_quizzes if q.score is not None]
        avg_score = sum(scores) / len(scores) if scores else 0.0

    # Days until exam
    now = datetime.now(UTC)
    exam_date = exam_prep.examDate
    if exam_date.tzinfo is None:
        exam_date = exam_date.replace(tzinfo=UTC)
    days_until = max(0, (exam_date - now).days)

    return {
        "examPrepId": exam_prep_id,
        "readinessScore": round(readiness, 1),
        "totalQuestions": total_questions,
        "masteredQuestions": mastered_questions,
        "learningQuestions": learning_questions,
        "newQuestions": new_questions,
        "totalQuizzes": len(completed_quizzes),
        "averageScore": round(avg_score, 1),
        "daysUntilExam": days_until,
        "topicProgress": topic_progress,
    }


async def get_weak_areas(db: Prisma, exam_prep_id: str, user_id: str) -> list[dict]:
    """Get questions the user struggles with most."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    # Get all questions with their attempts
    questions = await db.examquestion.find_many(
        where={"topic": {"examPrepId": exam_prep_id}},
        include={
            "attempts": {"order_by": {"createdAt": "desc"}},
            "topic": True,
        },
    )

    weak = []
    for q in questions:
        if not q.attempts:
            continue
        total_attempts = len(q.attempts)
        wrong_attempts = sum(1 for a in q.attempts if not a.isCorrect)
        if wrong_attempts > 0:
            error_rate = wrong_attempts / total_attempts
            weak.append(
                {
                    "questionId": q.id,
                    "questionText": q.questionText,
                    "topicTitle": q.topic.title if q.topic else "",
                    "totalAttempts": total_attempts,
                    "wrongAttempts": wrong_attempts,
                    "errorRate": round(error_rate * 100, 1),
                    "mastery": _calculate_question_mastery(q.attempts),
                }
            )

    # Sort by error rate descending
    weak.sort(key=lambda x: x["errorRate"], reverse=True)
    return weak[:20]


async def get_quiz_history(db: Prisma, exam_prep_id: str, user_id: str) -> list[dict]:
    """Get quiz session history for an exam prep."""
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    sessions = await db.examquizsession.find_many(
        where={"examPrepId": exam_prep_id, "completedAt": {"not": None}},
        order={"createdAt": "desc"},
        take=50,
    )

    return [
        {
            "id": s.id,
            "mode": s.mode,
            "totalQuestions": s.totalQuestions,
            "correctCount": s.correctCount,
            "score": s.score,
            "durationSeconds": s.durationSeconds,
            "completedAt": s.completedAt.isoformat() if s.completedAt else None,
            "createdAt": s.createdAt.isoformat(),
        }
        for s in sessions
    ]


def _calculate_question_mastery(attempts: list) -> str:
    """
    Calculate mastery level for a question based on attempt history.
    - NEW: Never attempted
    - LEARNING: Attempted but got wrong at least once recently
    - FAMILIAR: Got right once after getting wrong
    - MASTERED: Got right 2+ consecutive times
    """
    if not attempts:
        return "NEW"

    # Sort by most recent first
    sorted_attempts = sorted(attempts, key=lambda a: a.createdAt, reverse=True)

    # Count consecutive correct from most recent
    consecutive_correct = 0
    for attempt in sorted_attempts:
        if attempt.isCorrect:
            consecutive_correct += 1
        else:
            break

    if consecutive_correct >= 2:
        return "MASTERED"

    # Check if they ever got it right after getting it wrong
    has_wrong = any(not a.isCorrect for a in sorted_attempts)
    has_correct = any(a.isCorrect for a in sorted_attempts)

    if has_correct and has_wrong:
        return "FAMILIAR"

    if has_wrong:
        return "LEARNING"

    # Only one correct attempt
    return "FAMILIAR"
