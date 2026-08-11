"""
Personal Learning domain — API routes.

The learner's private environment: personalized home, notes, exam prep,
flashcards, study plans, documents, notifications, and more.

Mounted at: /api/v1/learning
"""

import logging
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from src.shared.auth import CurrentUser, OptionalCurrentUser

from . import models
from .services import (
    activity_feed_service,
    behaviour_service,
    discovery_service,
    exam_prep_service,
    flashcard_service,
    home_service,
    note_service,
    notification_service,
    onboarding_service,
    prep_snapshot_service,
    prepare_dashboard_service,
    quiz_engine,
    reflection_service,
    resource_service,
    study_plan_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["personal-learning"])


# ===========================================================================
# Home
# ===========================================================================


@router.get("/home", response_model=models.HomeResponse)
async def get_home(current_user: CurrentUser):
    """Get the personalized learning home — a Home, not a dashboard."""
    return await home_service.get_home(user_id=current_user.id)


@router.get("/dashboard", response_model=models.LearnDashboardResponse)
async def get_learn_dashboard(
    current_user: CurrentUser,
    courseLimit: int = Query(4, ge=1, le=8),
    pathLimit: int = Query(3, ge=1, le=5),
    recentLimit: int = Query(6, ge=1, le=10),
):
    """Compose the authenticated learner's bounded Learn dashboard."""
    from .services import learn_dashboard_service

    return await learn_dashboard_service.get_dashboard(
        user_id=current_user.id,
        course_limit=courseLimit,
        path_limit=pathLimit,
        recent_limit=recentLimit,
    )


# ===========================================================================
# Onboarding & Profile
# ===========================================================================


@router.post(
    "/onboarding/purpose",
    response_model=models.LearningProfileResponse,
    status_code=201,
)
async def set_purpose(body: models.PurposeSetRequest, current_user: CurrentUser):
    """Set the learner's purpose. First step of onboarding."""
    return await onboarding_service.set_purpose(user_id=current_user.id, purpose=body.purpose)


@router.post(
    "/onboarding/exam-details",
    response_model=models.LearningProfileResponse,
)
async def set_exam_details(body: models.ExamDetailsRequest, current_user: CurrentUser):
    """Set exam preparation details. For EXAM_PREP purpose learners."""
    return await onboarding_service.set_exam_details(
        user_id=current_user.id,
        exam_name=body.exam_name,
        exam_date=body.exam_date,
        subjects=body.subjects,
        goals=body.goals,
    )


@router.post(
    "/onboarding/skill-details",
    response_model=models.LearningProfileResponse,
)
async def set_skill_details(body: models.SkillDetailsRequest, current_user: CurrentUser):
    """Set skill building details. For SKILL_BUILDING purpose learners."""
    return await onboarding_service.set_skill_details(
        user_id=current_user.id,
        skill_name=body.skill_name,
        current_level=body.current_level,
        subjects=body.subjects,
        goals=body.goals,
    )


@router.get(
    "/onboarding/status",
    response_model=models.OnboardingStatusResponse,
)
async def get_onboarding_status(current_user: CurrentUser):
    """Get current onboarding status for progress polling."""
    return await onboarding_service.get_onboarding_status(user_id=current_user.id)


@router.post(
    "/onboarding/subjects",
    response_model=models.LearningProfileResponse,
)
async def set_subjects(body: models.SubjectsSetRequest, current_user: CurrentUser):
    """
    Set initial subjects and/or goals.

    DEPRECATED: Use /onboarding/exam-details or /onboarding/skill-details
    instead. Kept for backward compatibility.
    """
    return await onboarding_service.set_subjects(
        user_id=current_user.id, subjects=body.subjects, goals=body.goals
    )


@router.post("/onboarding/complete", status_code=204)
async def complete_onboarding(current_user: CurrentUser) -> None:
    """Complete onboarding and record the profile completion time."""
    await onboarding_service.complete_onboarding(user_id=current_user.id)


@router.get("/profile", response_model=models.LearningProfileResponse)
async def get_profile(current_user: CurrentUser):
    """Get the learner's current learning profile."""
    profile = await onboarding_service.get_profile(user_id=current_user.id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Learning profile not found. Complete onboarding first.",
        )
    return profile


@router.put(
    "/profile/llm-provider",
    response_model=models.LearningProfileResponse,
)
async def set_llm_provider(body: models.LlmProviderSetRequest, current_user: CurrentUser):
    """Set the provider used for the learner's personal-learning AI calls."""
    return await onboarding_service.set_preferred_llm_provider(
        user_id=current_user.id,
        provider=body.provider,
    )


# ===========================================================================
# Course Study
# ===========================================================================


# `GET /learning/courses` was removed. It returned an empty list unconditionally,
# which was indistinguishable from "this learner has no courses" and hid the fact
# that cross-domain integration was never implemented. Courses are owned by the
# Knowledge domain: use `GET /api/v1/knowledge/courses`.


@router.get("/courses/{course_id}/path", response_model=models.LearningPathResponse)
async def get_learning_path(course_id: str, current_user: CurrentUser):
    """Get learning path for a course."""
    raise HTTPException(status_code=501, detail="Course study integration pending")


@router.post("/courses/{course_id}/topics/{topic_id}/study", status_code=200)
async def record_topic_study(course_id: str, topic_id: str, current_user: CurrentUser):
    """Record a topic study activity."""
    return {"message": "Study activity recorded"}


@router.post("/courses/{course_id}/topics/{topic_id}/complete", status_code=200)
async def complete_topic(course_id: str, topic_id: str, current_user: CurrentUser):
    """Mark a topic as completed. Emits topic.completed event."""
    from .events import emit_topic_completed

    await emit_topic_completed(current_user.id, topic_id, course_id)
    return {"message": "Topic completed"}


# ===========================================================================
# Notes
# ===========================================================================


@router.post("/notes", response_model=models.NoteResponse, status_code=201)
async def create_note(body: models.NoteCreate, current_user: CurrentUser):
    """Create a personal note."""
    return await note_service.create_note(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )


@router.get("/notes", response_model=models.PaginatedResponse[models.NoteResponse])
async def list_notes(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    tag: str | None = Query(None),
    courseId: str | None = Query(None),
    topicId: str | None = Query(None),
    archived: bool | None = Query(False),
):
    """List notes using the canonical pagination envelope."""
    items, total = await note_service.list_notes(
        user_id=current_user.id,
        page=page,
        size=pageSize,
        search=search,
        tag=tag,
        course_id=courseId,
        topic_id=topicId,
        archived=archived,
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.NoteResponse](
        items=items,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.get("/notes/{note_id}", response_model=models.NoteResponse)
async def get_note(note_id: str, current_user: CurrentUser):
    """Get a note by ID."""
    return await note_service.get_note(user_id=current_user.id, note_id=note_id)


@router.patch("/notes/{note_id}", response_model=models.NoteResponse)
async def update_note(note_id: str, body: models.NoteUpdate, current_user: CurrentUser):
    """Update a note."""
    return await note_service.update_note(
        user_id=current_user.id, note_id=note_id, data=body.model_dump(exclude_unset=True)
    )


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str, current_user: CurrentUser):
    """Delete a note."""
    deleted = await note_service.delete_note(user_id=current_user.id, note_id=note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")


@router.post(
    "/notes/{note_id}/attachments", response_model=models.NoteAttachmentResponse, status_code=201
)
async def add_attachment(
    note_id: str, body: models.NoteAttachmentCreate, current_user: CurrentUser
):
    """Add an attachment to a note."""
    return await note_service.add_attachment(
        user_id=current_user.id, note_id=note_id, data=body.model_dump()
    )


@router.delete("/notes/{note_id}/attachments/{attachment_id}", status_code=204)
async def remove_attachment(note_id: str, attachment_id: str, current_user: CurrentUser):
    """Remove an attachment from a note."""
    removed = await note_service.remove_attachment(
        user_id=current_user.id, note_id=note_id, attachment_id=attachment_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")


@router.post("/notes/{note_id}/summary", response_model=models.NoteResponse)
async def generate_summary(note_id: str, current_user: CurrentUser):
    """Generate AI summary for a note."""
    return await note_service.add_summary(user_id=current_user.id, note_id=note_id)


@router.post("/notes/{note_id}/retake", response_model=models.NoteResponse)
async def retake_note(note_id: str, current_user: CurrentUser):
    """AI-rewrite a note with improved formatting."""
    return await note_service.retake_note(user_id=current_user.id, note_id=note_id)


@router.post("/notes/{note_id}/import", response_model=models.MessageResponse)
async def import_note(note_id: str, body: models.NoteImportRequest, current_user: CurrentUser):
    """Import a personal note to a learning space."""
    await note_service.import_to_space(
        user_id=current_user.id, note_id=note_id, space_id=body.spaceId
    )
    return models.MessageResponse(message="Note imported successfully")


# ===========================================================================
# Preparations
# ===========================================================================


@router.get("/prepare/dashboard", response_model=models.PrepareDashboardResponse)
async def get_prepare_dashboard(
    current_user: CurrentUser,
    preparationLimit: int = Query(6, ge=1, le=12),
    topicLimit: int = Query(8, ge=1, le=20),
    sessionLimit: int = Query(6, ge=1, le=20),
    milestoneLimit: int = Query(6, ge=1, le=20),
):
    """Compose the authenticated learner's bounded Prepare dashboard.

    Deliberately mounted under `/prepare/` rather than `/preparations/dashboard`,
    which would be ambiguous with `/preparations/{prep_id}` and would depend on
    route declaration order.
    """
    return await prepare_dashboard_service.get_dashboard(
        user_id=current_user.id,
        preparation_limit=preparationLimit,
        topic_limit=topicLimit,
        session_limit=sessionLimit,
        milestone_limit=milestoneLimit,
    )


@router.post("/preparations", response_model=models.PrepSummaryResponse, status_code=201)
async def create_preparation(body: models.PrepCreateRequest, current_user: CurrentUser):
    """Create a new preparation."""
    return await exam_prep_service.create_preparation(
        user_id=current_user.id, data=body.model_dump()
    )


@router.get("/preparations", response_model=models.PaginatedResponse[models.PrepSummaryResponse])
async def list_preparations(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    status: models.PreparationStatus | None = Query(None),
    search: str | None = Query(None, max_length=200),
    # `pattern`, not the deprecated `regex`: FastAPI dropped `regex` from the
    # generated schema, so this parameter was missing from the published contract
    # and therefore from the generated client types.
    sortBy: Literal["date", "readiness"] | None = Query(None),
):
    """List preparations with optional sorting.

    sortBy:
    - None or "date": ordered by target date ascending (default)
    - "readiness": ordered by average topic mastery descending
    """
    items, total = await exam_prep_service.search_preparations(
        user_id=current_user.id,
        status=status,
        search=search,
        sort_by=sortBy,
        page=page,
        page_size=pageSize,
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.PrepSummaryResponse](
        items=items,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.get("/preparations/{prep_id}", response_model=models.PrepDetailResponse)
async def get_preparation(prep_id: str, current_user: CurrentUser):
    """Get a preparation with its derived progress and next recommended action.

    Progress comes from the same `prep_readiness` helper the dashboard and the
    Learn surface use, so a workspace header cannot disagree with the card that
    linked to it. Previously this returned the bare row, which carried no progress
    at all and left the workspace with nothing to render its header from.
    """
    return await exam_prep_service.get_preparation_detail(user_id=current_user.id, prep_id=prep_id)


@router.patch("/preparations/{prep_id}", response_model=models.PrepSummaryResponse)
async def update_preparation(
    prep_id: str, body: models.PrepUpdateRequest, current_user: CurrentUser
):
    """Update a preparation."""
    return await exam_prep_service.update_preparation(
        user_id=current_user.id, prep_id=prep_id, data=body.model_dump(exclude_unset=True)
    )


@router.delete("/preparations/{prep_id}", status_code=204)
async def delete_preparation(prep_id: str, current_user: CurrentUser):
    """Delete a preparation."""
    deleted = await exam_prep_service.delete_preparation(user_id=current_user.id, prep_id=prep_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preparation not found")


@router.post(
    "/preparations/{prep_id}/materials", response_model=models.PrepMaterialResponse, status_code=201
)
async def upload_material(
    prep_id: str, body: models.PrepMaterialCreateRequest, current_user: CurrentUser
):
    """Register material against a preparation."""
    return await exam_prep_service.upload_material(
        user_id=current_user.id, prep_id=prep_id, data=body.model_dump(by_alias=True)
    )


@router.post(
    "/preparations/{prep_id}/materials/upload",
    response_model=models.PrepMaterialSummary,
    status_code=201,
)
async def upload_material_file(
    prep_id: str,
    current_user: CurrentUser,
    file: UploadFile = File(..., description="The material file."),
    category: models.PrepMaterialCategory = Form("OTHER"),
    label: str | None = Form(None, max_length=200),
):
    """Upload a material file and register it against a preparation.

    The JSON create path requires a `url`, so a learner picking a file from disk had
    nowhere to send it: there was no upload endpoint in the API and no
    direct-to-storage path on the web client, which made the workspace's file
    picker and the create wizard's drag-and-drop impossible to implement honestly.

    Text is extracted where the format allows (plain text, markdown, PDFs with a
    text layer), because extracted text is what topic extraction reads.
    `hasExtractedText` reports whether anything was recovered, so the client can
    tell the learner that a scanned PDF will not produce topics.
    """
    material = await exam_prep_service.upload_material_file(
        user_id=current_user.id,
        prep_id=prep_id,
        file=file,
        category=category,
        label=label,
    )
    return models.PrepMaterialSummary(
        id=material.id,
        prep_id=material.prep_id,
        filename=material.filename,
        url=material.url,
        file_type=material.file_type,
        size=material.size,
        category=material.category,
        label=material.label,
        has_extracted_text=bool(material.extracted_text),
        created_at=material.created_at,
        updated_at=material.updated_at,
    )


@router.get("/preparations/{prep_id}/materials", response_model=list[models.PrepMaterialSummary])
async def list_materials(prep_id: str, current_user: CurrentUser):
    """List a preparation's materials. Excludes extracted text."""
    return await exam_prep_service.list_materials(user_id=current_user.id, prep_id=prep_id)


@router.patch(
    "/preparations/{prep_id}/materials/{material_id}",
    response_model=models.PrepMaterialResponse,
)
async def update_material(
    prep_id: str,
    material_id: str,
    body: models.PrepMaterialUpdateRequest,
    current_user: CurrentUser,
):
    """Update a material's category or label."""
    return await exam_prep_service.update_material(
        user_id=current_user.id,
        prep_id=prep_id,
        material_id=material_id,
        data=body.model_dump(exclude_unset=True),
    )


@router.delete("/preparations/{prep_id}/materials/{material_id}", status_code=204)
async def delete_material(prep_id: str, material_id: str, current_user: CurrentUser):
    """Remove a material from a preparation."""
    deleted = await exam_prep_service.delete_material(
        user_id=current_user.id, prep_id=prep_id, material_id=material_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Material not found")


@router.post(
    "/preparations/{prep_id}/extract-topics", response_model=list[models.PrepTopicResponse]
)
async def extract_topics(prep_id: str, current_user: CurrentUser):
    """Trigger AI topic extraction from materials."""
    return await exam_prep_service.extract_topics(user_id=current_user.id, prep_id=prep_id)


@router.get("/preparations/{prep_id}/topics", response_model=list[models.PrepTopicDetail])
async def list_topics(prep_id: str, current_user: CurrentUser):
    """List a preparation's topics with their band and question counts.

    The counts are included because the alternative is a request per topic against
    the paginated question bank for a number one grouped query already produces.
    """
    return await exam_prep_service.list_topics(user_id=current_user.id, prep_id=prep_id)


@router.patch("/preparations/{prep_id}/topics/{topic_id}", response_model=models.PrepTopicResponse)
async def update_topic(
    prep_id: str,
    topic_id: str,
    body: models.PrepTopicUpdateRequest,
    current_user: CurrentUser,
):
    """Update a topic belonging to a preparation."""
    return await exam_prep_service.update_topic(
        user_id=current_user.id,
        prep_id=prep_id,
        topic_id=topic_id,
        data=body.model_dump(exclude_unset=True),
    )


@router.delete("/preparations/{prep_id}/topics/{topic_id}", status_code=204)
async def delete_topic(prep_id: str, topic_id: str, current_user: CurrentUser):
    """Remove a topic from a preparation."""
    deleted = await exam_prep_service.delete_topic(
        user_id=current_user.id, prep_id=prep_id, topic_id=topic_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found")


@router.post("/preparations/{prep_id}/study-plan", response_model=models.StudyPlanResponse)
async def generate_prep_study_plan(prep_id: str, current_user: CurrentUser):
    """Generate a study plan for a preparation.

    Delegates the preconditions to the service, which refuses rather than producing
    a plan that cannot be followed — see `generate_preparation_plan`.
    """
    return await exam_prep_service.generate_preparation_plan(
        user_id=current_user.id, prep_id=prep_id
    )


@router.post("/preparations/{prep_id}/complete", response_model=models.PrepSummaryResponse)
async def mark_prep_completed(prep_id: str, current_user: CurrentUser):
    """Mark a preparation as completed."""
    return await exam_prep_service.mark_completed(user_id=current_user.id, prep_id=prep_id)


# ===========================================================================
# Quizzes
# ===========================================================================


@router.post(
    "/preparations/{prep_id}/quizzes",
    response_model=models.QuizSessionResponse,
    status_code=201,
    responses={
        403: {
            "model": models.UpgradeRequiredResponse,
            "description": (
                "The requested mode requires Maigie Plus. Declared so the client "
                "can render an upgrade path from a typed payload."
            ),
        }
    },
)
async def start_quiz(prep_id: str, body: models.QuizStartRequest, current_user: CurrentUser):
    """Start a quiz session.

    Questions come back without their answer key. `correctAnswer` and
    `explanation` are disclosed per question once the learner has answered it,
    either in the answer response or on a subsequent read of the session.
    """
    return await quiz_engine.start_quiz(
        user_id=current_user.id,
        prep_id=prep_id,
        mode=body.mode,
        topic_id=body.topic_id,
        question_count=body.question_count,
    )


@router.get(
    "/preparations/{prep_id}/questions",
    response_model=models.PaginatedResponse[models.PrepQuestionBankItem],
)
async def list_question_bank(
    prep_id: str,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    topicId: str | None = Query(None),
    difficulty: models.QuestionDifficulty | None = Query(None),
    source: models.QuestionSource | None = Query(None),
    flaggedOnly: bool = Query(False),
):
    """Browse a preparation's question bank.

    Only expressible since migration `008` promoted questions from being owned by
    a quiz session to being owned by the preparation.

    The answer key is **not** included: browsing must not be a way to read answers
    without practising.
    """
    items, total = await exam_prep_service.search_question_bank(
        user_id=current_user.id,
        prep_id=prep_id,
        topic_id=topicId,
        difficulty=difficulty,
        source=source,
        flagged_only=flaggedOnly,
        page=page,
        page_size=pageSize,
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.PrepQuestionBankItem](
        items=items,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.get(
    "/preparations/{prep_id}/timeline",
    response_model=models.PrepTimelineResponse,
)
async def get_prep_timeline(prep_id: str, current_user: CurrentUser):
    """A preparation's timeline.

    Derived from the linked study plan's items plus the target date, rather than
    from a separate milestone entity — a second source of truth for "what should I
    do by when" would drift from the plan the first time either changed.
    """
    return await exam_prep_service.get_timeline(user_id=current_user.id, prep_id=prep_id)


@router.get(
    "/preparations/{prep_id}/readiness-trend",
    response_model=models.PrepReadinessTrendResponse,
)
async def get_readiness_trend(
    prep_id: str,
    current_user: CurrentUser,
    days: int = Query(30, ge=1, le=180),
):
    """A preparation's readiness over time.

    Backed by daily snapshots, because mastery is a mutable value and a trend
    cannot be derived from it after the fact.

    Returns only days that were captured. A new preparation has no history, and the
    client must render that as "no data yet" rather than a line through one point.
    """
    return await prep_snapshot_service.get_trend(
        user_id=current_user.id, prep_id=prep_id, days=days
    )


@router.put(
    "/preparations/{prep_id}/questions/{question_id}/flag",
    response_model=models.PrepQuestionFlagResponse,
)
async def flag_question(
    prep_id: str,
    question_id: str,
    current_user: CurrentUser,
    body: models.PrepQuestionFlagRequest | None = None,
):
    """Flag a question for later review.

    `PUT` rather than `POST` because it is idempotent: flagging an already-flagged
    question updates the note and is otherwise a no-op, so a repeated tap is a
    success, not a conflict.
    """
    return await exam_prep_service.flag_question(
        user_id=current_user.id,
        prep_id=prep_id,
        question_id=question_id,
        note=body.note if body else None,
    )


@router.delete(
    "/preparations/{prep_id}/questions/{question_id}/flag",
    status_code=204,
)
async def unflag_question(prep_id: str, question_id: str, current_user: CurrentUser):
    """Remove a flag. Succeeds whether or not the question was flagged."""
    await exam_prep_service.unflag_question(
        user_id=current_user.id, prep_id=prep_id, question_id=question_id
    )


@router.get("/preparations/{prep_id}/quizzes", response_model=list[models.QuizSessionResponse])
async def list_quizzes(prep_id: str, current_user: CurrentUser):
    """List all quiz sessions for a preparation."""
    return await quiz_engine.list_prep_quizzes(user_id=current_user.id, prep_id=prep_id)


@router.post("/quizzes/{quiz_id}/answer", response_model=models.AnswerResultResponse)
async def submit_answer(quiz_id: str, body: models.AnswerSubmitRequest, current_user: CurrentUser):
    """Submit an answer to a quiz question."""
    return await quiz_engine.submit_answer(
        user_id=current_user.id, quiz_id=quiz_id, data=body.model_dump()
    )


@router.post(
    "/quizzes/{quiz_id}/questions/{question_id}/hint",
    response_model=models.QuizHintResponse,
)
async def request_quiz_hint(
    quiz_id: str,
    question_id: str,
    current_user: CurrentUser,
    level: int = Query(1, ge=1, le=2),
):
    """Ask for a hint on a question.

    Pulled by the learner, never pushed. Level 1 points at the concept; level 2 also
    eliminates one wrong multiple-choice option.

    The hint never contains the correct answer — generated hints that do are
    discarded at creation rather than stored. Only available before the question has
    been answered: afterwards the key has already been disclosed, and allowing it
    would let hint counts be run up after the fact.
    """
    return await quiz_engine.request_hint(
        user_id=current_user.id,
        quiz_id=quiz_id,
        question_id=question_id,
        level=level,
    )


@router.post("/quizzes/{quiz_id}/complete", response_model=models.QuizSummaryResponse)
async def complete_quiz(
    quiz_id: str,
    current_user: CurrentUser,
    body: models.QuizCompleteRequest | None = None,
):
    """Complete a quiz session."""
    return await quiz_engine.complete_quiz(
        user_id=current_user.id,
        quiz_id=quiz_id,
        duration_seconds=body.duration_seconds if body else None,
    )


@router.get("/quizzes/{quiz_id}", response_model=models.QuizSessionResponse)
async def get_quiz(quiz_id: str, current_user: CurrentUser):
    """Get a quiz session, for resuming it or reviewing a completed one.

    `correctAnswer` and `explanation` are populated for questions the learner has
    already answered, so resuming a session keeps the explanations they have
    earned without revealing the ones they have not reached. A completed session
    returns the full key.
    """
    return await quiz_engine.get_quiz(user_id=current_user.id, quiz_id=quiz_id)


# ===========================================================================
# Flashcards
# ===========================================================================


@router.post("/flashcards", response_model=models.FlashcardResponse, status_code=201)
async def create_flashcard(body: models.FlashcardCreate, current_user: CurrentUser):
    """Create a flashcard."""
    return await flashcard_service.create_flashcard(user_id=current_user.id, data=body.model_dump())


@router.get("/flashcards/due", response_model=list[models.FlashcardResponse])
async def get_due_flashcards(current_user: CurrentUser):
    """Get flashcards due for review."""
    return await flashcard_service.get_due_flashcards(user_id=current_user.id)


@router.post("/flashcards/{card_id}/review", response_model=models.FlashcardResponse)
async def review_flashcard(
    card_id: str, body: models.FlashcardReviewRequest, current_user: CurrentUser
):
    """Submit a flashcard review (quality 0-5)."""
    result = await flashcard_service.review_flashcard(
        user_id=current_user.id, card_id=card_id, quality=body.quality
    )
    if not result:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    return result


@router.get("/flashcards/stats", response_model=models.FlashcardStats)
async def get_flashcard_stats(current_user: CurrentUser):
    """Get flashcard statistics."""
    return await flashcard_service.get_statistics(user_id=current_user.id)


@router.post("/flashcards/generate/note/{note_id}", response_model=list[models.FlashcardResponse])
async def generate_from_note(note_id: str, current_user: CurrentUser):
    """Generate flashcards from a note using AI."""
    return await flashcard_service.generate_from_note(user_id=current_user.id, note_id=note_id)


@router.post("/flashcards/generate/topic/{topic_id}", response_model=list[models.FlashcardResponse])
async def generate_from_topic(topic_id: str, current_user: CurrentUser):
    """Generate flashcards from a topic using AI."""
    return await flashcard_service.generate_from_topic(user_id=current_user.id, topic_id=topic_id)


@router.get("/decks", response_model=list[models.DeckResponse])
async def list_decks(current_user: CurrentUser):
    """List all flashcard decks."""
    return await flashcard_service.list_decks(user_id=current_user.id)


@router.post("/decks", response_model=models.DeckResponse, status_code=201)
async def create_deck(body: models.DeckCreate, current_user: CurrentUser):
    """Create a flashcard deck."""
    return await flashcard_service.create_deck(user_id=current_user.id, data=body.model_dump())


@router.get("/decks/{deck_id}/flashcards", response_model=list[models.FlashcardResponse])
async def list_deck_flashcards(deck_id: str, current_user: CurrentUser):
    """List flashcards in a deck."""
    return await flashcard_service.list_deck_flashcards(user_id=current_user.id, deck_id=deck_id)


# ===========================================================================
# Saved Resources
# ===========================================================================


@router.post("/resources", response_model=models.SavedResourceResponse, status_code=201)
async def save_resource(body: models.SavedResourceCreate, current_user: CurrentUser):
    """Save a resource to personal library."""
    return await resource_service.save_resource(user_id=current_user.id, data=body.model_dump())


@router.get("/resources", response_model=models.PaginatedResponse[models.SavedResourceResponse])
async def list_resources(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sourceType: str | None = Query(None),
    search: str | None = Query(None),
):
    """List saved resources using the canonical pagination envelope."""
    items, total = await resource_service.list_resources(
        user_id=current_user.id,
        source_type=sourceType,
        search=search,
        page=page,
        page_size=pageSize,
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.SavedResourceResponse](
        items=items,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.delete("/resources/{resource_id}", status_code=204)
async def delete_resource(resource_id: str, current_user: CurrentUser):
    """Remove a resource from personal library."""
    deleted = await resource_service.delete_resource(
        user_id=current_user.id, resource_id=resource_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Resource not found")


@router.patch("/resources/{resource_id}/tags", response_model=models.SavedResourceResponse)
async def update_resource_tags(
    resource_id: str, body: models.SavedResourceTagUpdate, current_user: CurrentUser
):
    """Update tags on a saved resource."""
    result = await resource_service.update_tags(
        user_id=current_user.id, resource_id=resource_id, tags=body.tags
    )
    if not result:
        raise HTTPException(status_code=404, detail="Resource not found")
    return result


# ===========================================================================
# Study Plans
# ===========================================================================


@router.post("/study-plans", response_model=models.StudyPlanResponse, status_code=201)
async def create_study_plan(body: models.StudyPlanCreate, current_user: CurrentUser):
    """Generate a study plan."""
    return await study_plan_service.generate_plan(user_id=current_user.id, data=body.model_dump())


@router.get("/study-plans", response_model=list[models.StudyPlanResponse])
async def list_study_plans(current_user: CurrentUser):
    """List active study plans."""
    return await study_plan_service.list_plans(user_id=current_user.id)


@router.get("/study-plans/{plan_id}", response_model=models.StudyPlanResponse)
async def get_study_plan(plan_id: str, current_user: CurrentUser):
    """Get a study plan with items."""
    return await study_plan_service.get_plan(user_id=current_user.id, plan_id=plan_id)


@router.post(
    "/study-plans/{plan_id}/items/{item_id}/complete", response_model=models.StudyPlanResponse
)
async def complete_plan_item(plan_id: str, item_id: str, current_user: CurrentUser):
    """Complete a study plan item."""
    return await study_plan_service.complete_item(
        user_id=current_user.id, plan_id=plan_id, item_id=item_id
    )


# ===========================================================================
# Documents
# ===========================================================================


@router.post("/documents", response_model=models.DocumentResponse, status_code=201)
async def generate_document(body: models.DocumentGenerateRequest, current_user: CurrentUser):
    """Generate an academic document from a natural-language prompt (synchronous)."""
    from fastapi import HTTPException

    from .services import document_impl

    payload = body.model_dump()
    try:
        fmt = document_impl._normalize_format(payload.get("format", "pdf"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return await document_impl.create_from_prompt(
        user_id=current_user.id,
        doc_type=payload["type"],
        title=payload["title"],
        prompt=payload["prompt"],
        format=fmt,
        style=payload.get("style", "academic"),
        course_id=payload.get("courseId"),
        topic_id=payload.get("topicId"),
    )


# Document job ownership is recorded out-of-band because a Celery task id alone
# proves nothing about who queued it. The TTL is far longer than the task's
# 180-second time limit, so an in-flight job is always still attributable.
_DOCUMENT_JOB_OWNER_TTL_SECONDS = 86_400


def _document_job_owner_key(task_id: str) -> str:
    return f"personal_learning:document_job_owner:{task_id}"


@router.post("/documents/async", status_code=202)
async def generate_document_async(body: models.DocumentGenerateRequest, current_user: CurrentUser):
    """
    Queue a document generation job. Returns immediately with a task id.

    Use ``GET /documents/jobs/{task_id}`` to poll for status. When the job
    completes, the ``result`` field contains the full document record.
    """
    import uuid

    from src.shared.infrastructure import cache
    from src.workers.personal_learning_tasks import generate_document_task

    payload = body.model_dump()
    task_id = uuid.uuid4().hex

    # Record ownership before queueing. If this fails the job is never queued,
    # because a job whose owner cannot be verified could never be polled.
    owner_recorded = await cache.set(
        _document_job_owner_key(task_id),
        current_user.id,
        expire=_DOCUMENT_JOB_OWNER_TTL_SECONDS,
    )
    if not owner_recorded:
        logger.error(
            "Refusing to queue document job: owner could not be recorded",
            extra={"user_id": current_user.id},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document generation is temporarily unavailable",
        )

    generate_document_task.apply_async(
        task_id=task_id,
        kwargs={
            "user_id": current_user.id,
            "doc_type": payload["type"],
            "title": payload["title"],
            "prompt": payload["prompt"],
            "format": payload.get("format", "pdf"),
            "style": payload.get("style", "academic"),
            "course_id": payload.get("courseId"),
            "topic_id": payload.get("topicId"),
        },
    )
    return {"taskId": task_id, "status": "queued"}


@router.get("/documents/jobs/{task_id}")
async def get_document_job(task_id: str, current_user: CurrentUser):
    """
    Poll the status of a queued document generation job.

    Only the learner who queued the job can read it. Unknown, expired, and
    other learners' jobs are all reported as ``404`` so this endpoint cannot be
    used to discover that another learner's job exists.

    Response shape:
      - ``{"taskId": ..., "status": "queued|running|success|failed", "result": {...} | null}``
    """
    from celery.result import AsyncResult

    from src.shared.infrastructure import cache
    from src.workers.celery_app import celery_app

    owner_id = await cache.get(_document_job_owner_key(task_id))
    if owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document job not found")

    result = AsyncResult(task_id, app=celery_app)
    state = (result.state or "PENDING").lower()
    status_map = {
        "pending": "queued",
        "received": "queued",
        "started": "running",
        "retry": "running",
        "success": "success",
        "failure": "failed",
        "revoked": "failed",
    }
    friendly = status_map.get(state, state)

    body: dict = {"taskId": task_id, "status": friendly, "result": None}
    if result.successful():
        payload = result.result
        # Defence in depth: never hand back a document owned by someone else.
        if isinstance(payload, dict) and payload.get("user_id") not in (None, current_user.id):
            logger.error(
                "Document job result owner mismatch",
                extra={"user_id": current_user.id, "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Document job not found")
        body["result"] = payload
    elif result.failed():
        body["error"] = str(result.result) if result.result else "Unknown error"
    return body


@router.get("/documents", response_model=models.DocumentListResponse)
async def list_documents(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """List generated documents."""
    from .services import document_impl

    items, total = await document_impl.list_documents(
        user_id=current_user.id, page=page, page_size=pageSize
    )
    return models.DocumentListResponse(items=items, total=total, page=page, pageSize=pageSize)


@router.get("/documents/share/{share_id}", response_model=models.DocumentResponse)
async def get_shared_document(share_id: str, current_user: OptionalCurrentUser = None):
    """
    Get a document by share id.

    - Public documents are viewable by anyone.
    - The document's owner can also view their own unpublished documents
      via this endpoint (useful for previewing the share URL before publishing).
    """
    from .services import document_impl

    requester_id = current_user.id if current_user else None
    doc = await document_impl.get_by_share_id(share_id=share_id, requester_id=requester_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/{doc_id}", response_model=models.DocumentResponse)
async def get_document(doc_id: str, current_user: CurrentUser):
    """Get a document by ID."""
    from .services import document_impl

    return await document_impl.get_document(user_id=current_user.id, doc_id=doc_id)


@router.post("/documents/{doc_id}/publish", response_model=models.DocumentResponse)
async def publish_document(doc_id: str, current_user: CurrentUser):
    """Make a document public with a share link."""
    from .services import document_impl

    return await document_impl.publish_document(user_id=current_user.id, doc_id=doc_id)


# ===========================================================================
# Notifications
# ===========================================================================


@router.get("/notifications", response_model=list[models.NotificationResponse])
async def get_notifications(current_user: CurrentUser):
    """Get unread notifications."""
    return await notification_service.get_unread(user_id=current_user.id)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(notification_id: str, current_user: CurrentUser):
    """Mark a notification as read."""
    await notification_service.mark_read(user_id=current_user.id, notification_id=notification_id)


@router.post("/notifications/{notification_id}/dismiss", status_code=204)
async def dismiss_notification(notification_id: str, current_user: CurrentUser):
    """Dismiss a notification."""
    await notification_service.dismiss(user_id=current_user.id, notification_id=notification_id)


# ===========================================================================
# Discovery
# ===========================================================================


@router.get("/discovery", response_model=list[models.DiscoveryRecommendationResponse])
async def get_discovery(current_user: CurrentUser):
    """Get discovery recommendations."""
    return await discovery_service.get_recommendations(user_id=current_user.id)


@router.post("/discovery/{recommendation_id}/follow", status_code=204)
async def follow_recommendation(recommendation_id: str, current_user: CurrentUser):
    """Follow a recommendation."""
    await discovery_service.follow_recommendation(
        user_id=current_user.id, recommendation_id=recommendation_id
    )


@router.post("/discovery/{recommendation_id}/dismiss", status_code=204)
async def dismiss_recommendation(recommendation_id: str, current_user: CurrentUser):
    """Dismiss a recommendation."""
    await discovery_service.dismiss_recommendation(
        user_id=current_user.id, recommendation_id=recommendation_id
    )


# ===========================================================================
# Behaviour & Reflection
# ===========================================================================


@router.get("/behaviour/profile", response_model=models.BehaviourProfileResponse)
async def get_behaviour_profile(current_user: CurrentUser):
    """Get the learner's behaviour profile."""
    return await behaviour_service.get_behaviour_profile(user_id=current_user.id)


@router.get(
    "/reflections",
    response_model=models.PaginatedResponse[models.ReflectionResponse],
)
async def list_reflections(
    current_user: CurrentUser,
    type: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """List reflections using the canonical pagination envelope."""
    items, total = await reflection_service.list_reflections(
        user_id=current_user.id, type_filter=type, page=page, page_size=pageSize
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.ReflectionResponse](
        items=items,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.post("/reflections/generate", response_model=models.ReflectionResponse, status_code=201)
async def generate_reflection(body: models.ReflectionGenerateRequest, current_user: CurrentUser):
    """Generate a reflection."""
    return await reflection_service.generate_reflection(user_id=current_user.id, type=body.type)


@router.get("/reflections/{reflection_id}", response_model=models.ReflectionResponse)
async def get_reflection(reflection_id: str, current_user: CurrentUser):
    """Get a specific reflection."""
    return await reflection_service.get_reflection(
        user_id=current_user.id, reflection_id=reflection_id
    )


# ===========================================================================
# Chat (Personal Learning Context)
# ===========================================================================


@router.post("/chat")
async def send_chat_message(body: dict, current_user: CurrentUser):
    """Send a message to Maigie with personal learning context."""
    from .services import chat_helper

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=422, detail="Message is required")
    return await chat_helper.send_message(user_id=current_user.id, message=message)


# ===========================================================================
# Activity Feed
# ===========================================================================


@router.get("/activity-feed", response_model=models.ActivityFeedResponse)
async def get_activity_feed(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """Get unified activity feed (personal + collaborative)."""
    items, total = await activity_feed_service.list_feed(
        user_id=current_user.id, page=page, page_size=pageSize
    )
    return models.ActivityFeedResponse(items=items, total=total, page=page, pageSize=pageSize)


# ===========================================================================
# Commercial: Capabilities & Feature Tier
# ===========================================================================


@router.get("/capabilities")
async def get_capabilities(current_user: CurrentUser):
    """Get the user's feature tier and available/locked capabilities."""
    from .services import feature_tier_service

    summary = await feature_tier_service.get_capabilities_summary(user_id=current_user.id)
    return {
        "effectiveTier": summary.effective_tier,
        "isTrial": summary.is_trial,
        "trialDaysRemaining": summary.trial_days_remaining,
        "capabilities": [
            {
                "id": cap.id,
                "name": cap.name,
                "freeDescription": cap.free_description,
                "plusDescription": cap.plus_description,
                "userLevel": cap.user_level,
                "lockedFeatures": cap.locked_features,
                "upgradeValue": cap.upgrade_value,
            }
            for cap in summary.capabilities
        ],
    }


# ===========================================================================
# Commercial: Trial
# ===========================================================================


@router.post("/trial/start", status_code=201)
async def start_trial(current_user: CurrentUser):
    """Start a 7-day Plus trial."""
    from .services import trial_service

    try:
        trial_status = await trial_service.start_trial(user_id=current_user.id)
        return {
            "isActive": trial_status.is_active,
            "dayNumber": trial_status.day_number,
            "daysRemaining": trial_status.days_remaining,
            "startsAt": trial_status.started_at.isoformat() if trial_status.started_at else None,
            "endsAt": trial_status.ends_at.isoformat() if trial_status.ends_at else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trial/status")
async def get_trial_status(current_user: CurrentUser):
    """Get current trial status."""
    from .services import trial_service

    trial_status = await trial_service.get_trial_status(user_id=current_user.id)
    if not trial_status:
        return {"isActive": False, "expired": False, "trialAvailable": True}
    return {
        "isActive": trial_status.is_active,
        "dayNumber": trial_status.day_number,
        "daysRemaining": trial_status.days_remaining,
        "expired": trial_status.expired,
        "startsAt": trial_status.started_at.isoformat() if trial_status.started_at else None,
        "endsAt": trial_status.ends_at.isoformat() if trial_status.ends_at else None,
        "nextTrialAvailableAt": (
            trial_status.next_trial_available_at.isoformat()
            if trial_status.next_trial_available_at
            else None
        ),
        "showcaseSuggestions": (
            [
                {
                    "capabilityId": s.capability_id,
                    "title": s.title,
                    "description": s.description,
                    "actionUrl": s.action_url,
                    "reason": s.reason,
                }
                for s in await trial_service.get_showcase_suggestions(current_user.id)
            ]
            if trial_status.is_active
            else []
        ),
    }


@router.post("/trial/summary")
async def get_trial_summary(current_user: CurrentUser):
    """Generate trial summary (available after trial expiry)."""
    from .services import trial_service

    trial_status = await trial_service.get_trial_status(user_id=current_user.id)
    if not trial_status or trial_status.is_active:
        raise HTTPException(status_code=400, detail="Trial summary available only after trial ends")

    summary = await trial_service.generate_trial_summary(user_id=current_user.id)
    return {
        "trialDays": summary.trial_days,
        "plusFeaturesUsed": summary.plus_features_used,
        "learningOutcomes": summary.learning_outcomes,
        "whatYouWouldLose": summary.what_you_would_lose,
        "upgradeUrl": summary.upgrade_url,
    }


# ===========================================================================
# Commercial: Conversion Triggers
# ===========================================================================


@router.post("/triggers/{trigger_id}/dismiss", status_code=204)
async def dismiss_trigger(trigger_id: str, current_user: CurrentUser):
    """Dismiss a conversion trigger."""
    from .services import conversion_engine

    await conversion_engine.record_dismissal(user_id=current_user.id, trigger_id=trigger_id)


# ===========================================================================
# Commercial: Value Summary
# ===========================================================================


@router.get("/value-summary")
async def get_value_summary(current_user: CurrentUser):
    """Get current period value summary (for Plus subscribers)."""
    from .services import feature_tier_service, value_summary_service

    tier, _, _ = await feature_tier_service.get_effective_tier(current_user.id)
    if tier != "plus":
        raise HTTPException(status_code=403, detail="Value summary is for Plus subscribers")

    summary = await value_summary_service.generate_monthly_summary(user_id=current_user.id)
    return {
        "periodStart": summary.period_start.isoformat(),
        "periodEnd": summary.period_end.isoformat(),
        "headline": summary.headline,
        "detailMessage": summary.detail_message,
        "metrics": {
            "aiAssistedSessions": summary.ai_assisted_sessions,
            "documentsGenerated": summary.documents_generated,
            "timeSavedMinutes": summary.documents_time_saved_minutes,
            "flashcardsReviewed": summary.flashcards_reviewed,
            "studyPlanItemsCompleted": summary.study_plan_items_completed,
            "goalsAchieved": summary.goals_achieved,
            "quizzesTaken": summary.quizzes_taken,
        },
        "topFeaturesUsed": summary.top_features_used,
        "plusExclusiveFeaturesUsed": summary.plus_exclusive_features_used,
    }


# ===========================================================================
# Commercial: Milestones
# ===========================================================================


@router.get("/milestones")
async def get_milestones(current_user: CurrentUser):
    """Get achieved milestones."""
    from .services import milestone_service

    milestones = await milestone_service.get_achieved_milestones(user_id=current_user.id)
    return {
        "milestones": [
            {
                "milestoneId": m.milestone_id,
                "title": m.title,
                "achievedAt": m.achieved_at.isoformat(),
                "shareText": m.share_text,
                "referralLink": m.referral_link,
                "shareCardUrl": m.share_card_url,
                "icon": m.icon,
            }
            for m in milestones
        ]
    }


@router.post("/milestones/{milestone_id}/share")
async def share_milestone(milestone_id: str, current_user: CurrentUser):
    """Generate a share card for a milestone."""
    from .services import milestone_service

    try:
        card = await milestone_service.generate_share_card(
            user_id=current_user.id, milestone_id=milestone_id
        )
        return {
            "milestoneId": card.milestone_id,
            "title": card.title,
            "imageUrl": card.image_url,
            "shareText": card.share_text,
            "referralLink": card.referral_link,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ===========================================================================
# Commercial: Educator Transition
# ===========================================================================


@router.get("/educator-readiness")
async def get_educator_readiness(current_user: CurrentUser):
    """Get educator readiness evaluation."""
    from .services import transition_service

    readiness = await transition_service.evaluate_educator_readiness(user_id=current_user.id)
    return {
        "isReady": readiness.is_ready,
        "signalsMet": readiness.signals_met,
        "totalSignals": readiness.total_signals,
        "signals": readiness.signals,
        "message": readiness.message,
    }


@router.post("/educator-transition/space-trial", status_code=201)
async def start_space_trial(current_user: CurrentUser):
    """Start a Learning Space Plan trial for educator-ready users."""
    from .services import transition_service

    try:
        trial_status = await transition_service.start_space_trial(user_id=current_user.id)
        return {
            "isActive": trial_status.is_active,
            "startedAt": trial_status.started_at.isoformat() if trial_status.started_at else None,
            "endsAt": trial_status.ends_at.isoformat() if trial_status.ends_at else None,
            "maxLearners": trial_status.max_learners,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
