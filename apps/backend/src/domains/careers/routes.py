"""Careers domain — API routes.

Two routers:
- ``admin_router`` — staff-only job CMS (``/admin/content/jobs``) and application triage
  (``/admin/career-applications``). Mounted under ``/api/v1/admin``.
- ``public_router`` — the public careers page reads (``/careers/jobs``) and application submission
  (``/careers/applications``). Unauthenticated: an applicant is not a learner.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.shared.auth import StaffUser
from src.shared.database import get_session_factory, ilike_any

from . import models
from .db_models import CareerApplication, JobPosting

logger = logging.getLogger(__name__)

admin_router = APIRouter(tags=["careers"])
public_router = APIRouter(tags=["careers"])


def _naive_utc_now() -> datetime:
    """UTC now without tzinfo — the careers timestamp columns are ``timestamp without time zone``."""
    return datetime.now(UTC).replace(tzinfo=None)


def _job_response(row: JobPosting) -> models.JobPostingResponse:
    return models.JobPostingResponse(
        id=row.id,
        slug=row.slug,
        title=row.title,
        location=row.location,
        type=row.type,
        stage=row.stage,
        description=row.description,
        responsibilities=row.responsibilities or [],
        requirementsMustHave=row.requirements_must_have or [],
        requirementsNiceToHave=row.requirements_nice_to_have or [],
        successMetrics=row.success_metrics or [],
        whyRoleMatters=row.why_role_matters or [],
        compensation=row.compensation or [],
        published=row.published,
        sortOrder=row.sort_order,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _application_response(row: CareerApplication) -> models.CareerApplicationResponse:
    return models.CareerApplicationResponse(
        id=row.id,
        jobId=row.job_id,
        jobTitle=row.job_title,
        firstName=row.first_name,
        lastName=row.last_name,
        email=row.email,
        linkedinUrl=row.linkedin_url,
        portfolioUrl=row.portfolio_url,
        coverLetter=row.cover_letter,
        status=row.status,
        adminNotes=row.admin_notes,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


# ===========================================================================
# Admin — job CMS
# ===========================================================================


@admin_router.get("/content/jobs", response_model=list[models.JobPostingResponse])
async def list_jobs_admin(admin_user: StaffUser):
    """All job postings, published or not, ordered as the careers page would show them (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(JobPosting).order_by(JobPosting.sort_order, JobPosting.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [_job_response(r) for r in rows]


@admin_router.post("/content/jobs", response_model=models.JobPostingResponse, status_code=201)
async def create_job(body: models.JobPostingCreateRequest, admin_user: StaffUser):
    """Create a job posting (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    now = _naive_utc_now()
    row = JobPosting(
        slug=body.slug,
        title=body.title,
        location=body.location,
        type=body.type,
        stage=body.stage,
        description=body.description,
        responsibilities=body.responsibilities,
        requirements_must_have=body.requirementsMustHave,
        requirements_nice_to_have=body.requirementsNiceToHave,
        success_metrics=body.successMetrics,
        why_role_matters=body.whyRoleMatters,
        compensation=body.compensation,
        published=body.published if body.published is not None else True,
        sort_order=body.sortOrder if body.sortOrder is not None else 0,
        created_at=now,
        updated_at=now,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="A job with that slug already exists")
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="create_job_posting",
        resource_type="job_posting",
        resource_id=row.id,
        details={"slug": row.slug, "title": row.title, "published": row.published},
    )
    return _job_response(row)


@admin_router.get("/content/jobs/{job_id}", response_model=models.JobPostingResponse)
async def get_job_admin(job_id: str, admin_user: StaffUser):
    """Fetch one job posting (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(JobPosting).where(JobPosting.id == job_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Job posting not found")
    return _job_response(row)


_JOB_FIELD_MAP = {
    "slug": "slug",
    "title": "title",
    "location": "location",
    "type": "type",
    "stage": "stage",
    "description": "description",
    "responsibilities": "responsibilities",
    "requirementsMustHave": "requirements_must_have",
    "requirementsNiceToHave": "requirements_nice_to_have",
    "successMetrics": "success_metrics",
    "whyRoleMatters": "why_role_matters",
    "compensation": "compensation",
    "published": "published",
    "sortOrder": "sort_order",
}


@admin_router.patch("/content/jobs/{job_id}", response_model=models.JobPostingResponse)
async def update_job(job_id: str, body: models.JobPostingUpdateRequest, admin_user: StaffUser):
    """Update a job posting (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    changes = body.model_dump(exclude_unset=True)
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(JobPosting).where(JobPosting.id == job_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Job posting not found")
        for key, value in changes.items():
            setattr(row, _JOB_FIELD_MAP.get(key, key), value)
        row.updated_at = _naive_utc_now()
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="A job with that slug already exists")
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_job_posting",
        resource_type="job_posting",
        resource_id=job_id,
        details={"fields": sorted(changes.keys())},
    )
    return _job_response(row)


@admin_router.delete("/content/jobs/{job_id}")
async def delete_job(job_id: str, admin_user: StaffUser):
    """Delete a job posting (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(JobPosting).where(JobPosting.id == job_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Job posting not found")
        await session.delete(row)
        await session.commit()

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_job_posting",
        resource_type="job_posting",
        resource_id=job_id,
        details={"slug": row.slug},
    )
    return {"message": "Job posting deleted", "jobId": job_id}


# ===========================================================================
# Admin — career applications
# ===========================================================================


@admin_router.get("/career-applications", response_model=models.CareerApplicationListResponse)
async def list_applications(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    jobId: str | None = Query(None),
    search: str | None = Query(None),
):
    """List career applications, paginated and filterable (staff only)."""
    conditions = []
    if status:
        conditions.append(CareerApplication.status == status)
    if jobId:
        conditions.append(CareerApplication.job_id == jobId)
    if search:
        conditions.append(
            ilike_any(
                search,
                CareerApplication.first_name,
                CareerApplication.last_name,
                CareerApplication.email,
            )
        )

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(CareerApplication).where(*conditions)
            )
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(CareerApplication)
                    .where(*conditions)
                    .order_by(CareerApplication.created_at.desc())
                    .offset((page - 1) * pageSize)
                    .limit(pageSize)
                )
            )
            .scalars()
            .all()
        )

    return models.CareerApplicationListResponse(
        applications=[_application_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(page * pageSize) < total,
    )


@admin_router.get(
    "/career-applications/{application_id}", response_model=models.CareerApplicationResponse
)
async def get_application(application_id: str, admin_user: StaffUser):
    """Read one application (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(CareerApplication).where(CareerApplication.id == application_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _application_response(row)


@admin_router.patch(
    "/career-applications/{application_id}", response_model=models.CareerApplicationResponse
)
async def update_application(
    application_id: str, body: models.CareerApplicationUpdateRequest, admin_user: StaffUser
):
    """Triage an application — status and/or admin notes (staff only), audited."""
    from src.domains.admin.services.audit_service import log_admin_action

    if body.status is not None and body.status not in models.CAREER_APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid application status")

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(CareerApplication).where(CareerApplication.id == application_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Application not found")
        before = {"status": row.status, "adminNotes": row.admin_notes}
        if body.status is not None:
            row.status = body.status
        if body.adminNotes is not None:
            row.admin_notes = body.adminNotes
        row.updated_at = _naive_utc_now()
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_career_application",
        resource_type="career_application",
        resource_id=application_id,
        details={
            "before": before,
            "after": {"status": row.status, "adminNotes": row.admin_notes},
        },
    )
    return _application_response(row)


# ===========================================================================
# Public — careers page
# ===========================================================================


@public_router.get("/jobs", response_model=list[models.JobPostingResponse])
async def list_published_jobs():
    """Published job postings for the public careers page, in display order."""
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(JobPosting)
                    .where(JobPosting.published.is_(True))
                    .order_by(JobPosting.sort_order, JobPosting.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [_job_response(r) for r in rows]


@public_router.get("/jobs/{slug}", response_model=models.JobPostingResponse)
async def get_published_job(slug: str):
    """One published job by slug."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(JobPosting).where(JobPosting.slug == slug, JobPosting.published.is_(True))
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Job posting not found")
    return _job_response(row)


@public_router.post(
    "/applications", response_model=models.CareerApplicationResponse, status_code=201
)
async def submit_application(body: models.CareerApplicationCreateRequest, request: Request):
    """Submit a job application (public). The job must exist and be published."""
    for field, value in (
        ("firstName", body.firstName),
        ("lastName", body.lastName),
        ("email", body.email),
        ("linkedinUrl", body.linkedinUrl),
        ("coverLetter", body.coverLetter),
    ):
        if not value or not value.strip():
            raise HTTPException(status_code=400, detail=f"{field} is required")

    factory = get_session_factory()
    async with factory() as session:
        job = (
            await session.execute(
                select(JobPosting).where(
                    JobPosting.id == body.jobId, JobPosting.published.is_(True)
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Job posting not found or not open")

        now = _naive_utc_now()
        row = CareerApplication(
            job_id=job.id,
            job_title=job.title,
            first_name=body.firstName.strip(),
            last_name=body.lastName.strip(),
            email=body.email.strip(),
            linkedin_url=body.linkedinUrl.strip(),
            portfolio_url=body.portfolioUrl,
            cover_letter=body.coverLetter.strip(),
            status="NEW",
            user_agent=request.headers.get("user-agent"),
            source_ip=(request.client.host if request.client else None),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _application_response(row)
