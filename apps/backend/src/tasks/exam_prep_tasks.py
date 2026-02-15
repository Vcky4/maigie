"""
Exam Prep processing tasks (Celery).

- Processes uploaded materials: extract text, identify topics, parse past questions
- Generates AI question bank from study materials
- Sends WebSocket progress updates
"""

from __future__ import annotations

import logging
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task

logger = logging.getLogger(__name__)

TASK_PROCESS_EXAM_PREP = "exam_prep.process_materials"


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _emit_progress(user_id: str, exam_prep_id: str, stage: str, progress: int, message: str):
    """Emit WebSocket progress event via Redis."""
    try:
        import json
        from src.core.cache import cache

        event = json.dumps(
            {
                "type": "exam_prep_progress",
                "examPrepId": exam_prep_id,
                "userId": user_id,
                "stage": stage,
                "progress": progress,
                "message": message,
            }
        )
        await cache.get_client().publish("ws:events", event)
    except Exception as e:
        logger.warning("Failed to emit progress: %s", e)


async def _process_exam_prep(exam_prep_id: str, user_id: str) -> dict[str, Any]:
    """
    Main processing pipeline:
    1. Re-extract text from materials (with async OCR for images)
    2. Extract topics from all materials
    3. Parse past questions from PAST_QUESTION materials
    4. Generate AI questions for each topic
    5. Generate study plan
    6. Transition to ACTIVE
    """
    from src.core.database import db
    from src.services.exam_prep_service import (
        generate_study_plan,
        save_questions,
        save_topics,
        transition_status,
    )
    from src.services.llm_service import (
        extract_exam_topics,
        extract_past_paper_questions,
        generate_exam_questions,
    )
    from src.services.text_extraction_service import extract_text_from_file_async

    await _ensure_db_connected()

    # Fetch exam prep with materials
    exam_prep = await db.examprep.find_first(
        where={"id": exam_prep_id, "userId": user_id},
        include={"materials": True},
    )
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    materials = exam_prep.materials or []
    total_steps = 4 + len(materials)  # extract + topics + questions + study plan
    current_step = 0

    # --- Step 1: Re-extract text from materials (with OCR for images) ---
    await _emit_progress(
        user_id, exam_prep_id, "extracting_text", 10, "Extracting text from materials..."
    )

    study_texts = []
    past_question_texts = []

    for material in materials:
        current_step += 1
        progress = int((current_step / total_steps) * 30)

        # If extracted text is missing or material is an image, re-extract
        if not material.extractedText:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(material.url)
                    if response.status_code == 200:
                        extracted = await extract_text_from_file_async(
                            response.content,
                            material.filename,
                            material.fileType,
                        )
                        if extracted:
                            await db.examprepmaterial.update(
                                where={"id": material.id},
                                data={"extractedText": extracted},
                            )
                            material.extractedText = extracted
            except Exception as e:
                logger.warning("Failed to re-extract text for %s: %s", material.filename, e)

        if material.extractedText:
            if material.category == "PAST_QUESTION":
                past_question_texts.append(
                    {
                        "text": material.extractedText,
                        "materialId": material.id,
                        "label": material.label,
                    }
                )
            else:
                study_texts.append(material.extractedText)

        await _emit_progress(
            user_id, exam_prep_id, "extracting_text", progress, f"Processed {material.filename}..."
        )

    # --- Step 2: Extract topics ---
    await _emit_progress(
        user_id, exam_prep_id, "extracting_topics", 35, "Identifying key topics..."
    )

    all_texts = study_texts + [pq["text"] for pq in past_question_texts]
    topics_data = await extract_exam_topics(exam_prep.subject, all_texts)

    saved_topics = await save_topics(db, exam_prep_id, topics_data)
    topic_map = {t.title: t.id for t in saved_topics}

    await _emit_progress(
        user_id, exam_prep_id, "extracting_topics", 45, f"Found {len(saved_topics)} topics"
    )

    # --- Step 3: Parse past questions ---
    await _emit_progress(
        user_id, exam_prep_id, "parsing_questions", 50, "Parsing past exam questions..."
    )

    past_questions_by_topic: dict[str, list[dict]] = {t.title: [] for t in saved_topics}
    total_past_questions = 0

    for pq_data in past_question_texts:
        try:
            parsed = await extract_past_paper_questions(pq_data["text"], exam_prep.subject)
            for q in parsed:
                q["materialId"] = pq_data["materialId"]
                if pq_data.get("label"):
                    q["year"] = pq_data["label"]

                # Assign to best matching topic (simple keyword matching)
                assigned = False
                for topic_title in topic_map:
                    if _text_matches_topic(q["questionText"], topic_title):
                        past_questions_by_topic[topic_title].append(q)
                        assigned = True
                        break

                if not assigned and saved_topics:
                    # Assign to first topic as fallback
                    first_topic = saved_topics[0].title
                    past_questions_by_topic[first_topic].append(q)

                total_past_questions += 1
        except Exception as e:
            logger.warning("Failed to parse past questions: %s", e)

    # Save past questions
    for topic_title, questions in past_questions_by_topic.items():
        if questions and topic_title in topic_map:
            await save_questions(db, topic_map[topic_title], questions)

    await _emit_progress(
        user_id,
        exam_prep_id,
        "parsing_questions",
        65,
        f"Extracted {total_past_questions} past paper questions",
    )

    # --- Step 4: Generate AI questions for each topic ---
    await _emit_progress(
        user_id, exam_prep_id, "generating_questions", 70, "Generating practice questions..."
    )

    total_ai_questions = 0
    combined_study_text = "\n\n".join(study_texts)

    for i, topic in enumerate(saved_topics):
        try:
            # Get existing questions for this topic to avoid duplicates
            existing_qs = past_questions_by_topic.get(topic.title, [])
            existing_texts = [q["questionText"] for q in existing_qs]

            # Generate 3-5 questions per topic
            count = max(3, min(5, 10 - len(existing_qs)))
            ai_questions = await generate_exam_questions(
                subject=exam_prep.subject,
                topic_title=topic.title,
                context_text=combined_study_text,
                count=count,
                existing_questions=existing_texts,
            )

            if ai_questions:
                await save_questions(db, topic.id, ai_questions)
                total_ai_questions += len(ai_questions)

        except Exception as e:
            logger.warning("Failed to generate questions for topic %s: %s", topic.title, e)

        progress = 70 + int(((i + 1) / len(saved_topics)) * 20)
        await _emit_progress(
            user_id,
            exam_prep_id,
            "generating_questions",
            progress,
            f"Generated questions for {topic.title}",
        )

    # --- Step 5: Generate study plan ---
    await _emit_progress(user_id, exam_prep_id, "generating_plan", 92, "Creating study plan...")

    try:
        blocks_created = await generate_study_plan(db, exam_prep_id, user_id)
    except Exception as e:
        logger.warning("Failed to generate study plan: %s", e)
        blocks_created = 0

    # --- Step 6: Transition to ACTIVE ---
    await transition_status(db, exam_prep_id, user_id, "ACTIVE")

    await _emit_progress(
        user_id,
        exam_prep_id,
        "complete",
        100,
        f"Ready! {total_past_questions} past questions + {total_ai_questions} AI questions generated.",
    )

    return {
        "topicsCreated": len(saved_topics),
        "pastQuestionsExtracted": total_past_questions,
        "aiQuestionsGenerated": total_ai_questions,
        "studyBlocksCreated": blocks_created,
    }


def _text_matches_topic(text: str, topic_title: str) -> bool:
    """Simple keyword matching to assign a question to a topic."""
    text_lower = text.lower()
    topic_words = topic_title.lower().split()
    # If at least half the topic words appear in the question
    matches = sum(1 for word in topic_words if word in text_lower and len(word) > 3)
    return matches >= max(1, len(topic_words) // 2)


@register_task(
    name=TASK_PROCESS_EXAM_PREP,
    description="Process exam prep materials: extract topics, parse questions, generate question bank",
    category="exam_prep",
    tags=["exam_prep", "ai", "processing"],
)
@task(name=TASK_PROCESS_EXAM_PREP, bind=True, max_retries=2)
def process_exam_prep_task(self: Any, exam_prep_id: str, user_id: str) -> dict[str, Any]:
    """Background task: process exam prep materials and generate question bank."""
    return run_async_in_celery(_process_exam_prep(exam_prep_id, user_id))


# ---------------------------------------------------------------------------
# Daily exam prep reminder task
# ---------------------------------------------------------------------------

TASK_EXAM_PREP_REMINDERS = "exam_prep.daily_reminders"


async def _send_exam_prep_reminders() -> dict[str, Any]:
    """
    Daily task: For each user with active exam preps,
    check weak areas and create review schedule blocks if exam is approaching.
    Also marks completed exam preps (past exam date).
    """
    from datetime import UTC, datetime, timedelta
    from src.core.database import db

    await _ensure_db_connected()

    now = datetime.now(UTC)

    # Mark completed exam preps (exam date has passed)
    expired = await db.examprep.find_many(
        where={
            "examDate": {"lt": now},
            "status": "ACTIVE",
        },
    )
    for ep in expired:
        await db.examprep.update(
            where={"id": ep.id},
            data={"status": "COMPLETED"},
        )

    # For active exam preps with upcoming exams (within 30 days)
    upcoming_cutoff = now + timedelta(days=30)
    active_preps = await db.examprep.find_many(
        where={
            "status": "ACTIVE",
            "examDate": {"gte": now, "lte": upcoming_cutoff},
        },
        include={
            "topics": {"include": {"questions": {"include": {"attempts": True}}}},
        },
    )

    reminders_created = 0
    for ep in active_preps:
        days_until = (ep.examDate - now).days

        # Determine review intensity based on proximity to exam
        if days_until <= 3:
            # Cramming mode: daily weak-area reviews
            pass  # Users should be studying intensively; handled by their quiz usage
        elif days_until <= 7:
            # Check if they have weak areas that need attention
            from src.services.exam_prep_service import _calculate_question_mastery

            weak_count = 0
            for topic in ep.topics or []:
                for q in topic.questions or []:
                    mastery = _calculate_question_mastery(q.attempts)
                    if mastery in ("LEARNING", "NEW"):
                        weak_count += 1

            if weak_count > 5:
                # Create a review block for today
                review_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
                if review_start < now:
                    review_start += timedelta(days=1)
                review_end = review_start + timedelta(minutes=45)

                # Check if block already exists for today
                existing = await db.scheduleblock.find_first(
                    where={
                        "userId": ep.userId,
                        "examPrepId": ep.id,
                        "startAt": {"gte": now.replace(hour=0, minute=0)},
                        "endAt": {"lte": now.replace(hour=23, minute=59)},
                    }
                )
                if not existing:
                    await db.scheduleblock.create(
                        data={
                            "userId": ep.userId,
                            "title": f"Review Weak Areas: {ep.subject}",
                            "description": f"You have {weak_count} questions to review. Focus on weak areas!",
                            "startAt": review_start,
                            "endAt": review_end,
                            "examPrepId": ep.id,
                        }
                    )
                    reminders_created += 1

    return {
        "expiredMarked": len(expired),
        "remindersCreated": reminders_created,
        "activePrepsChecked": len(active_preps),
    }


@register_task(
    name=TASK_EXAM_PREP_REMINDERS,
    description="Daily exam prep reminders and status updates",
    category="exam_prep",
    tags=["exam_prep", "schedule", "reminders"],
)
@task(name=TASK_EXAM_PREP_REMINDERS, bind=True, max_retries=2)
def exam_prep_reminders_task(self: Any) -> dict[str, Any]:
    """Run daily: send exam prep reminders and mark completed preps."""
    return run_async_in_celery(_send_exam_prep_reminders())


def register_exam_prep_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for exam prep."""
    from src.tasks.schedules import DAILY_AT_8AM, register_periodic_task

    register_periodic_task(
        name="exam_prep.daily_reminders",
        schedule=DAILY_AT_8AM,
        task=TASK_EXAM_PREP_REMINDERS,
    )


# Register with Celery Beat when module is loaded
register_exam_prep_beat_tasks()
