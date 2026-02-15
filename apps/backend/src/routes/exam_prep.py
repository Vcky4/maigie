"""
Exam Prep API routes.
CRUD for exam prep, material upload (categorized), topics, quiz sessions,
progress tracking, and study plan generation.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from src.dependencies import DBDep, PremiumUser
from src.services.exam_prep_service import (
    add_material,
    create_exam_prep,
    delete_material,
    delete_topic,
    generate_study_plan,
    get_exam_prep_progress,
    get_quiz_history,
    get_weak_areas,
    update_exam_prep,
    update_material,
    update_topic,
    transition_status,
)
from src.services.exam_quiz_service import (
    complete_quiz,
    start_quiz,
    submit_answer,
)
from src.services.storage_service import storage_service
from src.services.text_extraction_service import extract_text_from_file
from src.services.usage_tracking_service import increment_feature_usage
from src.utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exam-prep", tags=["exam-prep"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExamPrepCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    exam_date: str = Field(..., description="ISO date string e.g. 2025-03-15")
    description: str | None = None


class ExamPrepUpdate(BaseModel):
    subject: str | None = None
    exam_date: str | None = None
    description: str | None = None
    status: str | None = None


class MaterialUpdate(BaseModel):
    category: str | None = None
    label: str | None = None


class TopicUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class QuizStartRequest(BaseModel):
    mode: str = Field(
        ..., description="FULL_PRACTICE, WEAK_AREAS, TOPIC_FOCUS, PAST_PAPER_SIM, QUICK_REVIEW"
    )
    topic_id: str | None = None
    question_count: int | None = None


class AnswerSubmitRequest(BaseModel):
    question_id: str
    user_answer: str
    time_taken_seconds: int | None = None


class QuizCompleteRequest(BaseModel):
    duration_seconds: int | None = None


# Response models


class ExamPrepMaterialResponse(BaseModel):
    id: str
    filename: str
    url: str
    extractedText: str | None = None
    fileType: str | None = None
    size: int | None = None
    category: str = "OTHER"
    label: str | None = None
    createdAt: str


class ExamPrepTopicResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    order: int = 0
    questionCount: int = 0


class ExamPrepResponse(BaseModel):
    id: str
    subject: str
    examDate: str
    description: str | None = None
    status: str = "SETUP"
    materials: list[ExamPrepMaterialResponse] = []
    topics: list[ExamPrepTopicResponse] = []
    totalQuestions: int = 0
    createdAt: str
    updatedAt: str


class ExamPrepListResponse(BaseModel):
    items: list[ExamPrepResponse]
    total: int


# ---------------------------------------------------------------------------
# Helper to serialize exam prep
# ---------------------------------------------------------------------------


def _serialize_exam_prep(e, materials=None, topics=None) -> ExamPrepResponse:
    """Serialize an exam prep record to response format."""
    mat_list = materials if materials is not None else (e.materials or [])
    topic_list = topics if topics is not None else (getattr(e, "topics", None) or [])

    total_questions = 0
    topic_responses = []
    for t in topic_list:
        q_count = len(t.questions) if hasattr(t, "questions") and t.questions else 0
        total_questions += q_count
        topic_responses.append(
            ExamPrepTopicResponse(
                id=t.id,
                title=t.title,
                description=t.description,
                order=t.order,
                questionCount=q_count,
            )
        )

    return ExamPrepResponse(
        id=e.id,
        subject=e.subject,
        examDate=e.examDate.isoformat(),
        description=e.description,
        status=e.status,
        materials=[
            ExamPrepMaterialResponse(
                id=m.id,
                filename=m.filename,
                url=m.url,
                extractedText=m.extractedText,
                fileType=m.fileType,
                size=m.size,
                category=m.category,
                label=m.label,
                createdAt=m.createdAt.isoformat(),
            )
            for m in mat_list
        ],
        topics=topic_responses,
        totalQuestions=total_questions,
        createdAt=e.createdAt.isoformat(),
        updatedAt=e.updatedAt.isoformat(),
    )


# ---------------------------------------------------------------------------
# CRUD Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=ExamPrepListResponse)
async def list_exam_preps(
    current_user: PremiumUser,
    db_client: DBDep,
):
    """List all exam preps for the current user."""
    items = await db_client.examprep.find_many(
        where={"userId": current_user.id},
        include={
            "materials": True,
            "topics": {"include": {"questions": True}},
        },
        order={"examDate": "asc"},
    )
    return ExamPrepListResponse(
        items=[_serialize_exam_prep(e) for e in items],
        total=len(items),
    )


@router.post("", response_model=ExamPrepResponse, status_code=status.HTTP_201_CREATED)
async def create_exam_prep_route(
    body: ExamPrepCreate,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Create a new exam prep."""
    try:
        raw = body.exam_date.replace("Z", "+00:00").strip()
        if "T" not in raw and "+" not in raw and raw.count("-") == 2:
            raw = f"{raw}T23:59:00"
        exam_date = datetime.fromisoformat(raw)
        if exam_date.tzinfo is None:
            exam_date = exam_date.replace(tzinfo=UTC)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid exam_date format. Use ISO format e.g. 2025-03-15",
        )

    now = datetime.now(UTC)
    if exam_date.date() < now.date():
        raise HTTPException(status_code=400, detail="Exam date must be in the future")

    exam_prep = await create_exam_prep(
        db=db_client,
        user_id=current_user.id,
        subject=body.subject,
        exam_date=exam_date,
        description=body.description,
    )

    # Re-fetch with relations
    exam_prep = await db_client.examprep.find_unique(
        where={"id": exam_prep.id},
        include={"materials": True, "topics": {"include": {"questions": True}}},
    )

    return _serialize_exam_prep(exam_prep)


@router.get("/{exam_prep_id}", response_model=ExamPrepResponse)
async def get_exam_prep(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get a single exam prep by ID."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id},
        include={
            "materials": True,
            "topics": {"include": {"questions": True}, "order_by": {"order": "asc"}},
        },
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    return _serialize_exam_prep(exam_prep)


@router.patch("/{exam_prep_id}", response_model=ExamPrepResponse)
async def update_exam_prep_route(
    exam_prep_id: str,
    body: ExamPrepUpdate,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Update an exam prep."""
    data = {}
    if body.subject is not None:
        data["subject"] = body.subject
    if body.description is not None:
        data["description"] = body.description
    if body.status is not None:
        data["status"] = body.status
    if body.exam_date is not None:
        try:
            raw = body.exam_date.replace("Z", "+00:00").strip()
            if "T" not in raw and "+" not in raw and raw.count("-") == 2:
                raw = f"{raw}T23:59:00"
            data["examDate"] = datetime.fromisoformat(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid exam_date format")

    try:
        await update_exam_prep(db_client, exam_prep_id, current_user.id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    exam_prep = await db_client.examprep.find_unique(
        where={"id": exam_prep_id},
        include={"materials": True, "topics": {"include": {"questions": True}}},
    )
    return _serialize_exam_prep(exam_prep)


@router.delete("/{exam_prep_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam_prep(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Delete an exam prep (cascade deletes materials, topics, questions, quiz sessions)."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    await db_client.examprep.delete(where={"id": exam_prep_id})


# ---------------------------------------------------------------------------
# Material Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{exam_prep_id}/materials",
    response_model=ExamPrepMaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
    file: UploadFile = File(...),
    category: str = Form("OTHER"),
    label: str | None = Form(None),
):
    """Upload a material file to an exam prep with category."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    try:
        await increment_feature_usage(current_user, "file_uploads", db_client=db_client)
    except SubscriptionLimitError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)

    # Read file content for extraction before upload (upload consumes the stream)
    file_content = await file.read()
    await file.seek(0)  # Reset for upload

    result = await storage_service.upload_file(
        file,
        path=f"exam-prep/{exam_prep_id}",
    )

    # Extract text for RAG/quiz generation
    extracted_text = None
    try:
        if file_content:
            extracted_text = extract_text_from_file(
                file_content,
                file.filename or result["filename"],
                file.content_type,
            )
    except Exception as e:
        logger.warning("Failed to extract text from %s: %s", file.filename, e)

    # Validate category
    valid_categories = ["TEXTBOOK", "NOTES", "PAST_QUESTION", "LINK", "SLIDE", "OTHER"]
    if category not in valid_categories:
        category = "OTHER"

    material = await add_material(
        db=db_client,
        exam_prep_id=exam_prep_id,
        user_id=current_user.id,
        filename=result["filename"],
        url=result["url"],
        category=category,
        label=label,
        extracted_text=extracted_text,
        file_type=file.content_type,
        size=result.get("size"),
    )

    return ExamPrepMaterialResponse(
        id=material.id,
        filename=material.filename,
        url=material.url,
        extractedText=material.extractedText,
        fileType=material.fileType,
        size=material.size,
        category=material.category,
        label=material.label,
        createdAt=material.createdAt.isoformat(),
    )


@router.patch("/{exam_prep_id}/materials/{material_id}", response_model=ExamPrepMaterialResponse)
async def update_material_route(
    exam_prep_id: str,
    material_id: str,
    body: MaterialUpdate,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Update material category or label."""
    try:
        material = await update_material(
            db_client,
            material_id,
            exam_prep_id,
            current_user.id,
            data=body.model_dump(exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ExamPrepMaterialResponse(
        id=material.id,
        filename=material.filename,
        url=material.url,
        extractedText=material.extractedText,
        fileType=material.fileType,
        size=material.size,
        category=material.category,
        label=material.label,
        createdAt=material.createdAt.isoformat(),
    )


@router.delete("/{exam_prep_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_route(
    exam_prep_id: str,
    material_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Delete a material from an exam prep."""
    try:
        await delete_material(db_client, material_id, exam_prep_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Processing / AI Analysis
# ---------------------------------------------------------------------------


@router.post("/{exam_prep_id}/process")
async def process_materials(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Trigger AI analysis of materials: extract topics, parse past questions, generate question bank."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id},
        include={"materials": True},
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    if not exam_prep.materials:
        raise HTTPException(status_code=400, detail="No materials uploaded yet")

    # Transition to PROCESSING
    await transition_status(db_client, exam_prep_id, current_user.id, "PROCESSING")

    # Dispatch background task
    from src.tasks.exam_prep_tasks import process_exam_prep_task

    process_exam_prep_task.delay(exam_prep_id, current_user.id)

    return {"status": "processing", "message": "AI analysis started. This may take a few minutes."}


@router.get("/{exam_prep_id}/processing-status")
async def get_processing_status(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Check processing status."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id},
        include={"topics": {"include": {"questions": True}}},
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    total_questions = sum(len(t.questions) for t in (exam_prep.topics or []))

    return {
        "status": exam_prep.status,
        "topicCount": len(exam_prep.topics or []),
        "questionCount": total_questions,
    }


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


@router.get("/{exam_prep_id}/topics")
async def list_topics(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """List extracted topics for an exam prep."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    topics = await db_client.exampreptopic.find_many(
        where={"examPrepId": exam_prep_id},
        include={"questions": True},
        order={"order": "asc"},
    )

    return {
        "topics": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "order": t.order,
                "questionCount": len(t.questions) if t.questions else 0,
            }
            for t in topics
        ]
    }


@router.patch("/{exam_prep_id}/topics/{topic_id}")
async def update_topic_route(
    exam_prep_id: str,
    topic_id: str,
    body: TopicUpdate,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Edit a topic."""
    try:
        topic = await update_topic(
            db_client,
            topic_id,
            exam_prep_id,
            current_user.id,
            data=body.model_dump(exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"id": topic.id, "title": topic.title, "description": topic.description}


@router.delete("/{exam_prep_id}/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic_route(
    exam_prep_id: str,
    topic_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Delete a topic (cascades to questions)."""
    try:
        await delete_topic(db_client, topic_id, exam_prep_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Question Bank
# ---------------------------------------------------------------------------


@router.get("/{exam_prep_id}/questions")
async def list_questions(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
    topic_id: str | None = Query(None),
    source: str | None = Query(None),
    difficulty: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List questions in the question bank (filterable)."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    where: dict = {"topic": {"examPrepId": exam_prep_id}}
    if topic_id:
        where["topicId"] = topic_id
    if source:
        where["source"] = source
    if difficulty:
        where["difficulty"] = difficulty

    questions = await db_client.examquestion.find_many(
        where=where,
        include={"topic": True, "attempts": True},
        take=limit,
        skip=offset,
        order={"createdAt": "desc"},
    )

    total = await db_client.examquestion.count(where=where)

    from src.services.exam_prep_service import _calculate_question_mastery

    return {
        "questions": [
            {
                "id": q.id,
                "questionText": q.questionText,
                "questionType": q.questionType,
                "options": q.options,
                "correctAnswer": q.correctAnswer,
                "explanation": q.explanation,
                "source": q.source,
                "difficulty": q.difficulty,
                "year": q.year,
                "topicId": q.topicId,
                "topicTitle": q.topic.title if q.topic else "",
                "mastery": _calculate_question_mastery(q.attempts),
                "attemptCount": len(q.attempts) if q.attempts else 0,
                "tags": q.tags,
            }
            for q in questions
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{exam_prep_id}/questions/stats")
async def question_stats(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get question bank statistics."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    questions = await db_client.examquestion.find_many(
        where={"topic": {"examPrepId": exam_prep_id}},
        include={"attempts": True},
    )

    from src.services.exam_prep_service import _calculate_question_mastery

    stats = {
        "total": len(questions),
        "bySource": {"PAST_QUESTION": 0, "AI_GENERATED": 0},
        "byDifficulty": {"EASY": 0, "MEDIUM": 0, "HARD": 0},
        "byMastery": {"NEW": 0, "LEARNING": 0, "FAMILIAR": 0, "MASTERED": 0},
        "byType": {"MULTIPLE_CHOICE": 0, "TRUE_FALSE": 0, "SHORT_ANSWER": 0, "FILL_IN_BLANK": 0},
    }

    for q in questions:
        stats["bySource"][q.source] = stats["bySource"].get(q.source, 0) + 1
        stats["byDifficulty"][q.difficulty] = stats["byDifficulty"].get(q.difficulty, 0) + 1
        stats["byType"][q.questionType] = stats["byType"].get(q.questionType, 0) + 1
        mastery = _calculate_question_mastery(q.attempts)
        stats["byMastery"][mastery] = stats["byMastery"].get(mastery, 0) + 1

    return stats


# ---------------------------------------------------------------------------
# Quiz Routes
# ---------------------------------------------------------------------------


@router.post("/{exam_prep_id}/quiz")
async def start_quiz_route(
    exam_prep_id: str,
    body: QuizStartRequest,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Start a quiz session."""
    try:
        result = await start_quiz(
            db_client,
            exam_prep_id,
            current_user.id,
            mode=body.mode,
            topic_id=body.topic_id,
            question_count=body.question_count,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{exam_prep_id}/quiz/{quiz_session_id}/answer")
async def submit_answer_route(
    exam_prep_id: str,
    quiz_session_id: str,
    body: AnswerSubmitRequest,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Submit an answer for a quiz question."""
    # Verify ownership
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    try:
        result = await submit_answer(
            db_client,
            quiz_session_id,
            body.question_id,
            body.user_answer,
            body.time_taken_seconds,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{exam_prep_id}/quiz/{quiz_session_id}/complete")
async def complete_quiz_route(
    exam_prep_id: str,
    quiz_session_id: str,
    body: QuizCompleteRequest,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Complete a quiz session and get results."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    try:
        result = await complete_quiz(
            db_client,
            quiz_session_id,
            body.duration_seconds,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{exam_prep_id}/quiz/{quiz_session_id}")
async def get_quiz_session(
    exam_prep_id: str,
    quiz_session_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get quiz session details."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    session = await db_client.examquizsession.find_unique(
        where={"id": quiz_session_id},
        include={"attempts": {"include": {"question": {"include": {"topic": True}}}}},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found")

    return {
        "id": session.id,
        "mode": session.mode,
        "totalQuestions": session.totalQuestions,
        "correctCount": session.correctCount,
        "score": session.score,
        "durationSeconds": session.durationSeconds,
        "completedAt": session.completedAt.isoformat() if session.completedAt else None,
        "createdAt": session.createdAt.isoformat(),
        "attempts": [
            {
                "id": a.id,
                "questionId": a.questionId,
                "questionText": a.question.questionText if a.question else "",
                "topicTitle": a.question.topic.title if a.question and a.question.topic else "",
                "userAnswer": a.userAnswer,
                "isCorrect": a.isCorrect,
                "explanation": a.question.explanation if a.question else "",
            }
            for a in (session.attempts or [])
        ],
    }


# ---------------------------------------------------------------------------
# Progress & Analytics
# ---------------------------------------------------------------------------


@router.get("/{exam_prep_id}/progress")
async def get_progress(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get overall progress and readiness."""
    try:
        return await get_exam_prep_progress(db_client, exam_prep_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{exam_prep_id}/progress/weak-areas")
async def get_weak_areas_route(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get weakest questions."""
    try:
        return {"weakAreas": await get_weak_areas(db_client, exam_prep_id, current_user.id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{exam_prep_id}/progress/history")
async def get_history(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get quiz score history."""
    try:
        return {"history": await get_quiz_history(db_client, exam_prep_id, current_user.id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Study Plan
# ---------------------------------------------------------------------------


@router.post("/{exam_prep_id}/generate-study-plan")
async def regenerate_study_plan(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Regenerate study schedule blocks for the exam prep."""
    try:
        count = await generate_study_plan(db_client, exam_prep_id, current_user.id)
        return {"blocksCreated": count}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
