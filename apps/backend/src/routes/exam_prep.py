"""
Exam Prep API routes.
CRUD for exam prep, material upload, study plan, quiz generation.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from src.core.database import db
from src.dependencies import CurrentUser, DBDep, PremiumUser
from src.services.exam_prep_service import add_material, create_exam_prep, generate_study_plan
from src.services.storage_service import storage_service
from src.services.text_extraction_service import extract_text_from_file
from src.services.usage_tracking_service import increment_feature_usage
from src.utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exam-prep", tags=["exam-prep"])


# --- Pydantic models ---


class ExamPrepCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    exam_date: str = Field(..., description="ISO date string e.g. 2025-03-15")
    description: str | None = None


class ExamPrepMaterialResponse(BaseModel):
    id: str
    filename: str
    url: str
    extractedText: str | None = None
    fileType: str | None = None
    size: int | None = None
    createdAt: str


class ExamPrepResponse(BaseModel):
    id: str
    subject: str
    examDate: str
    description: str | None = None
    materials: list[ExamPrepMaterialResponse] = []
    createdAt: str
    updatedAt: str


class ExamPrepListResponse(BaseModel):
    items: list[ExamPrepResponse]
    total: int


# --- Routes ---


@router.get("", response_model=ExamPrepListResponse)
async def list_exam_preps(
    current_user: PremiumUser,
    db_client: DBDep,
):
    """List all exam preps for the current user."""
    items = await db_client.examprep.find_many(
        where={"userId": current_user.id},
        include={"materials": True},
        order={"examDate": "asc"},
    )
    return ExamPrepListResponse(
        items=[
            ExamPrepResponse(
                id=e.id,
                subject=e.subject,
                examDate=e.examDate.isoformat(),
                description=e.description,
                materials=[
                    ExamPrepMaterialResponse(
                        id=m.id,
                        filename=m.filename,
                        url=m.url,
                        extractedText=m.extractedText,
                        fileType=m.fileType,
                        size=m.size,
                        createdAt=m.createdAt.isoformat(),
                    )
                    for m in e.materials
                ],
                createdAt=e.createdAt.isoformat(),
                updatedAt=e.updatedAt.isoformat(),
            )
            for e in items
        ],
        total=len(items),
    )


@router.post("", response_model=ExamPrepResponse, status_code=status.HTTP_201_CREATED)
async def create_exam_prep_route(
    body: ExamPrepCreate,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Create a new exam prep and generate initial study schedule."""
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
        raise HTTPException(
            status_code=400,
            detail="Exam date must be in the future",
        )

    exam_prep = await create_exam_prep(
        db=db_client,
        user_id=current_user.id,
        subject=body.subject,
        exam_date=exam_date,
        description=body.description,
    )

    materials = await db_client.examprepmaterial.find_many(where={"examPrepId": exam_prep.id})

    return ExamPrepResponse(
        id=exam_prep.id,
        subject=exam_prep.subject,
        examDate=exam_prep.examDate.isoformat(),
        description=exam_prep.description,
        materials=[
            ExamPrepMaterialResponse(
                id=m.id,
                filename=m.filename,
                url=m.url,
                extractedText=m.extractedText,
                fileType=m.fileType,
                size=m.size,
                createdAt=m.createdAt.isoformat(),
            )
            for m in materials
        ],
        createdAt=exam_prep.createdAt.isoformat(),
        updatedAt=exam_prep.updatedAt.isoformat(),
    )


@router.get("/{exam_prep_id}", response_model=ExamPrepResponse)
async def get_exam_prep(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Get a single exam prep by ID."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id},
        include={"materials": True},
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    return ExamPrepResponse(
        id=exam_prep.id,
        subject=exam_prep.subject,
        examDate=exam_prep.examDate.isoformat(),
        description=exam_prep.description,
        materials=[
            ExamPrepMaterialResponse(
                id=m.id,
                filename=m.filename,
                url=m.url,
                extractedText=m.extractedText,
                fileType=m.fileType,
                size=m.size,
                createdAt=m.createdAt.isoformat(),
            )
            for m in exam_prep.materials
        ],
        createdAt=exam_prep.createdAt.isoformat(),
        updatedAt=exam_prep.updatedAt.isoformat(),
    )


@router.delete("/{exam_prep_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam_prep(
    exam_prep_id: str,
    current_user: PremiumUser,
    db_client: DBDep,
):
    """Delete an exam prep (cascade deletes materials)."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    await db_client.examprep.delete(where={"id": exam_prep_id})


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
):
    """Upload a material file to an exam prep."""
    exam_prep = await db_client.examprep.find_first(
        where={"id": exam_prep_id, "userId": current_user.id}
    )
    if not exam_prep:
        raise HTTPException(status_code=404, detail="Exam prep not found")

    try:
        await increment_feature_usage(current_user, "file_uploads", db_client=db_client)
    except SubscriptionLimitError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)

    result = await storage_service.upload_file(
        file,
        path=f"exam-prep/{exam_prep_id}",
    )

    # Extract text for RAG/quiz generation
    extracted_text = None
    try:
        content = await file.read()
        if content:
            extracted_text = extract_text_from_file(
                content,
                file.filename or result["filename"],
                file.content_type,
            )
    except Exception as e:
        logger.warning("Failed to extract text from %s: %s", file.filename, e)

    material = await add_material(
        db=db_client,
        exam_prep_id=exam_prep_id,
        user_id=current_user.id,
        filename=result["filename"],
        url=result["url"],
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
        createdAt=material.createdAt.isoformat(),
    )


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
