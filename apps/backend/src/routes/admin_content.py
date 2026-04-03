"""
Admin CMS: blog posts and job postings (super admins + content managers).
"""

import logging
import re

from fastapi import APIRouter, HTTPException, status

from src.dependencies import DBDep, StaffAdminUser
from src.models.cms import (
    BlogPostAdminCreate,
    BlogPostAdminResponse,
    BlogPostAdminUpdate,
    JobPostingAdminCreate,
    JobPostingAdminResponse,
    JobPostingAdminUpdate,
)
from src.services.audit_service import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/content", tags=["admin"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _require_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug must be lowercase letters, numbers, and hyphens only",
        )


def _blog_admin(row) -> BlogPostAdminResponse:
    return BlogPostAdminResponse.model_validate(row)


def _job_admin(row) -> JobPostingAdminResponse:
    return JobPostingAdminResponse.model_validate(row)


# --- Blog ---


@router.get("/blog", response_model=list[BlogPostAdminResponse])
async def admin_list_blog_posts(
    _admin: StaffAdminUser,
    db: DBDep,
):
    rows = await db.blogpost.find_many(order={"publishedAt": "desc"})
    return [_blog_admin(r) for r in rows]


@router.post("/blog", response_model=BlogPostAdminResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_blog_post(
    body: BlogPostAdminCreate,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    _require_slug(body.slug)
    existing = await db.blogpost.find_unique(where={"slug": body.slug})
    if existing:
        raise HTTPException(status_code=400, detail="A post with this slug already exists")

    row = await db.blogpost.create(
        data={
            "slug": body.slug.strip(),
            "title": body.title.strip(),
            "description": body.description.strip(),
            "content": body.content,
            "authorName": body.authorName.strip(),
            "authorRole": body.authorRole.strip() if body.authorRole else None,
            "publishedAt": body.publishedAt,
            "tags": body.tags,
            "category": body.category.strip(),
            "coverImage": body.coverImage.strip() if body.coverImage else None,
            "readTime": body.readTime,
            "featured": body.featured,
            "published": body.published,
        }
    )
    await log_admin_action(
        admin_user.id,
        "create_blog_post",
        "blog_post",
        resource_id=row.id,
        details={"slug": row.slug},
        db_client=db,
    )
    return _blog_admin(row)


@router.get("/blog/{post_id}", response_model=BlogPostAdminResponse)
async def admin_get_blog_post(
    post_id: str,
    _admin: StaffAdminUser,
    db: DBDep,
):
    row = await db.blogpost.find_unique(where={"id": post_id})
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")
    return _blog_admin(row)


@router.patch("/blog/{post_id}", response_model=BlogPostAdminResponse)
async def admin_update_blog_post(
    post_id: str,
    body: BlogPostAdminUpdate,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.blogpost.find_unique(where={"id": post_id})
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    data: dict = {}
    if body.slug is not None:
        _require_slug(body.slug)
        slug_clean = body.slug.strip()
        other = await db.blogpost.find_unique(where={"slug": slug_clean})
        if other and other.id != post_id:
            raise HTTPException(status_code=400, detail="Slug already in use")
        data["slug"] = slug_clean
    if body.title is not None:
        data["title"] = body.title.strip()
    if body.description is not None:
        data["description"] = body.description.strip()
    if body.content is not None:
        data["content"] = body.content
    if body.authorName is not None:
        data["authorName"] = body.authorName.strip()
    if body.authorRole is not None:
        data["authorRole"] = body.authorRole.strip() if body.authorRole else None
    if body.publishedAt is not None:
        data["publishedAt"] = body.publishedAt
    if body.tags is not None:
        data["tags"] = body.tags
    if body.category is not None:
        data["category"] = body.category.strip()
    if body.coverImage is not None:
        data["coverImage"] = body.coverImage.strip() if body.coverImage else None
    if body.readTime is not None:
        data["readTime"] = body.readTime
    if body.featured is not None:
        data["featured"] = body.featured
    if body.published is not None:
        data["published"] = body.published

    if not data:
        return _blog_admin(row)

    updated = await db.blogpost.update(where={"id": post_id}, data=data)
    await log_admin_action(
        admin_user.id,
        "update_blog_post",
        "blog_post",
        resource_id=post_id,
        details={"fields": list(data.keys())},
        db_client=db,
    )
    return _blog_admin(updated)


@router.delete("/blog/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_blog_post(
    post_id: str,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.blogpost.find_unique(where={"id": post_id})
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")
    await db.blogpost.delete(where={"id": post_id})
    await log_admin_action(
        admin_user.id,
        "delete_blog_post",
        "blog_post",
        resource_id=post_id,
        details={"slug": row.slug},
        db_client=db,
    )


# --- Jobs ---


@router.get("/jobs", response_model=list[JobPostingAdminResponse])
async def admin_list_jobs(
    _admin: StaffAdminUser,
    db: DBDep,
):
    rows = await db.jobposting.find_many(order={"sortOrder": "asc"})
    return [_job_admin(r) for r in rows]


@router.post("/jobs", response_model=JobPostingAdminResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_job(
    body: JobPostingAdminCreate,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    _require_slug(body.slug)
    existing = await db.jobposting.find_unique(where={"slug": body.slug})
    if existing:
        raise HTTPException(status_code=400, detail="A job with this slug already exists")

    row = await db.jobposting.create(
        data={
            "slug": body.slug.strip(),
            "title": body.title.strip(),
            "location": body.location.strip(),
            "type": body.type.strip(),
            "stage": body.stage.strip(),
            "description": body.description.strip(),
            "responsibilities": body.responsibilities,
            "requirementsMustHave": body.requirementsMustHave,
            "requirementsNiceToHave": body.requirementsNiceToHave,
            "successMetrics": body.successMetrics,
            "whyRoleMatters": body.whyRoleMatters,
            "compensation": body.compensation,
            "published": body.published,
            "sortOrder": body.sortOrder,
        }
    )
    await log_admin_action(
        admin_user.id,
        "create_job_posting",
        "job_posting",
        resource_id=row.id,
        details={"slug": row.slug},
        db_client=db,
    )
    return _job_admin(row)


@router.get("/jobs/{job_id}", response_model=JobPostingAdminResponse)
async def admin_get_job(
    job_id: str,
    _admin: StaffAdminUser,
    db: DBDep,
):
    row = await db.jobposting.find_unique(where={"id": job_id})
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_admin(row)


@router.patch("/jobs/{job_id}", response_model=JobPostingAdminResponse)
async def admin_update_job(
    job_id: str,
    body: JobPostingAdminUpdate,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.jobposting.find_unique(where={"id": job_id})
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    data: dict = {}
    if body.slug is not None:
        _require_slug(body.slug)
        slug_clean = body.slug.strip()
        other = await db.jobposting.find_unique(where={"slug": slug_clean})
        if other and other.id != job_id:
            raise HTTPException(status_code=400, detail="Slug already in use")
        data["slug"] = slug_clean
    if body.title is not None:
        data["title"] = body.title.strip()
    if body.location is not None:
        data["location"] = body.location.strip()
    if body.type is not None:
        data["type"] = body.type.strip()
    if body.stage is not None:
        data["stage"] = body.stage.strip()
    if body.description is not None:
        data["description"] = body.description.strip()
    if body.responsibilities is not None:
        data["responsibilities"] = body.responsibilities
    if body.requirementsMustHave is not None:
        data["requirementsMustHave"] = body.requirementsMustHave
    if body.requirementsNiceToHave is not None:
        data["requirementsNiceToHave"] = body.requirementsNiceToHave
    if body.successMetrics is not None:
        data["successMetrics"] = body.successMetrics
    if body.whyRoleMatters is not None:
        data["whyRoleMatters"] = body.whyRoleMatters
    if body.compensation is not None:
        data["compensation"] = body.compensation
    if body.published is not None:
        data["published"] = body.published
    if body.sortOrder is not None:
        data["sortOrder"] = body.sortOrder

    if not data:
        return _job_admin(row)

    updated = await db.jobposting.update(where={"id": job_id}, data=data)
    await log_admin_action(
        admin_user.id,
        "update_job_posting",
        "job_posting",
        resource_id=job_id,
        details={"fields": list(data.keys())},
        db_client=db,
    )
    return _job_admin(updated)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_job(
    job_id: str,
    admin_user: StaffAdminUser,
    db: DBDep,
):
    row = await db.jobposting.find_unique(where={"id": job_id})
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.jobposting.delete(where={"id": job_id})
    await log_admin_action(
        admin_user.id,
        "delete_job_posting",
        "job_posting",
        resource_id=job_id,
        details={"slug": row.slug},
        db_client=db,
    )
