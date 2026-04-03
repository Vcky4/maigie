"""
Admin-only career application management.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.dependencies import DBDep, StaffAdminUser
from src.models.careers import (
    CareerApplicationAdminUpdate,
    CareerApplicationListResponse,
    CareerApplicationResponse,
)
from src.services.audit_service import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/career-applications", tags=["admin"])


def _to_response(row) -> CareerApplicationResponse:
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


@router.get("", response_model=CareerApplicationListResponse)
async def list_career_applications(
    admin_user: StaffAdminUser,
    db: DBDep,
    status_filter: str | None = Query(None, alias="status"),
    job_id: str | None = Query(None, alias="jobId"),
    search: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
):
    where: dict = {}
    if status_filter:
        where["status"] = status_filter
    if job_id:
        where["jobId"] = job_id
    if search:
        term = search.strip()
        where["OR"] = [
            {"email": {"contains": term, "mode": "insensitive"}},
            {"firstName": {"contains": term, "mode": "insensitive"}},
            {"lastName": {"contains": term, "mode": "insensitive"}},
            {"jobTitle": {"contains": term, "mode": "insensitive"}},
        ]

    total = await db.careerapplication.count(where=where)
    skip = (page - 1) * page_size
    rows = await db.careerapplication.find_many(
        where=where,
        skip=skip,
        take=page_size,
        order={"createdAt": "desc"},
    )

    return CareerApplicationListResponse(
        applications=[_to_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=page_size,
        hasMore=skip + len(rows) < total,
    )


@router.get("/{application_id}", response_model=CareerApplicationResponse)
async def get_career_application(
    application_id: str,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.careerapplication.find_unique(where={"id": application_id})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return _to_response(row)


@router.patch("/{application_id}", response_model=CareerApplicationResponse)
async def update_career_application(
    application_id: str,
    body: CareerApplicationAdminUpdate,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.careerapplication.find_unique(where={"id": application_id})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    data: dict = {}
    if body.status is not None:
        data["status"] = body.status
    if body.adminNotes is not None:
        data["adminNotes"] = body.adminNotes

    if not data:
        return _to_response(row)

    updated = await db.careerapplication.update(where={"id": application_id}, data=data)

    await log_admin_action(
        admin_user.id,
        "update_career_application",
        "career_application",
        resource_id=application_id,
        details={"fields": list(data.keys())},
        db_client=db,
    )

    logger.info("Admin %s updated career application %s", admin_user.email, application_id)
    return _to_response(updated)
