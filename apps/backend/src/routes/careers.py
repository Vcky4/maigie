"""
Public career application submission (no auth required).
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from src.core.database import db
from src.models.careers import CareerApplicationCreate, CareerApplicationResponse
from src.models.cms import JobPostingPublic

logger = logging.getLogger(__name__)


def _job_to_public(row) -> JobPostingPublic:
    return JobPostingPublic(
        id=row.slug,
        title=row.title,
        location=row.location,
        type=row.type,
        stage=row.stage,
        description=row.description,
        responsibilities=list(row.responsibilities or []),
        requirementsMustHave=list(row.requirementsMustHave or []),
        requirementsNiceToHave=list(row.requirementsNiceToHave or []),
        successMetrics=list(row.successMetrics or []),
        whyRoleMatters=list(row.whyRoleMatters or []),
        compensation=list(row.compensation or []),
    )


router = APIRouter(prefix="/api/v1/careers", tags=["careers"])


@router.get("/jobs", response_model=list[JobPostingPublic])
async def list_public_jobs():
    rows = await db.jobposting.find_many(
        where={"published": True},
        order={"sortOrder": "asc"},
    )
    return [_job_to_public(r) for r in rows]


@router.get("/jobs/{slug}", response_model=JobPostingPublic)
async def get_public_job(slug: str):
    row = await db.jobposting.find_first(where={"slug": slug, "published": True})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_to_public(row)


@router.post(
    "/applications",
    response_model=CareerApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_career_application(payload: CareerApplicationCreate, request: Request):
    """
    Submit a job application from the marketing careers site.
    """
    try:
        user_agent = request.headers.get("user-agent")
        source_ip = request.client.host if request.client else None

        row = await db.careerapplication.create(
            data={
                "jobId": payload.jobId.strip(),
                "jobTitle": payload.jobTitle.strip(),
                "firstName": payload.firstName.strip(),
                "lastName": payload.lastName.strip(),
                "email": str(payload.email).strip().lower(),
                "linkedinUrl": payload.linkedinUrl.strip(),
                "portfolioUrl": payload.portfolioUrl.strip() if payload.portfolioUrl else None,
                "coverLetter": payload.coverLetter.strip(),
                "userAgent": user_agent,
                "sourceIp": source_ip,
            }
        )

        logger.info(
            "Career application created id=%s jobId=%s email=%s", row.id, row.jobId, row.email
        )

        return CareerApplicationResponse(
            id=row.id,
            jobId=row.jobId,
            jobTitle=row.jobTitle,
            firstName=row.firstName,
            lastName=row.lastName,
            email=row.email,
            linkedinUrl=row.linkedinUrl,
            portfolioUrl=row.portfolioUrl,
            coverLetter=row.coverLetter,
            status=row.status,
            adminNotes=row.adminNotes,
            createdAt=row.createdAt.isoformat(),
            updatedAt=row.updatedAt.isoformat(),
        )
    except Exception as e:
        logger.error("Career application submit failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit application",
        ) from e
