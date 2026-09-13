"""Content domain — API routes.

Two routers:
- ``admin_router`` — staff-only blog CMS (``/admin/content/blog``). Mounted under ``/api/v1/admin``.
- ``public_router`` — published-post reads for the public site (``/blog``). Unauthenticated.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.shared.auth import StaffUser
from src.shared.database import get_session_factory, ilike_any

from . import models
from .db_models import BlogPost, ContentCalendarEntry

logger = logging.getLogger(__name__)

admin_router = APIRouter(tags=["content"])
public_router = APIRouter(tags=["content"])


def _naive(dt: datetime) -> datetime:
    """Drop tzinfo (converting to UTC first) — the blog timestamp columns are without time zone."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _response(row: BlogPost) -> models.BlogPostResponse:
    return models.BlogPostResponse(
        id=row.id,
        slug=row.slug,
        title=row.title,
        description=row.description,
        content=row.content,
        authorName=row.author_name,
        authorRole=row.author_role,
        publishedAt=row.published_at,
        tags=row.tags or [],
        category=row.category,
        coverImage=row.cover_image,
        readTime=row.read_time,
        featured=row.featured,
        published=row.published,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


# ===========================================================================
# Admin — blog CMS
# ===========================================================================


@admin_router.get("/content/blog", response_model=list[models.BlogPostResponse])
async def list_blog_admin(admin_user: StaffUser):
    """All blog posts, published or not, newest first (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (await session.execute(select(BlogPost).order_by(BlogPost.published_at.desc())))
            .scalars()
            .all()
        )
    return [_response(r) for r in rows]


@admin_router.post("/content/blog", response_model=models.BlogPostResponse, status_code=201)
async def create_blog_post(body: models.BlogPostCreateRequest, admin_user: StaffUser):
    """Create a blog post (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    now = _naive_utc_now()
    row = BlogPost(
        slug=body.slug,
        title=body.title,
        description=body.description,
        content=body.content,
        author_name=body.authorName,
        author_role=body.authorRole,
        published_at=_naive(body.publishedAt),
        tags=body.tags,
        category=body.category,
        cover_image=body.coverImage,
        read_time=body.readTime,
        featured=body.featured if body.featured is not None else False,
        published=body.published if body.published is not None else False,
        created_at=now,
        updated_at=now,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="A post with that slug already exists")
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="create_blog_post",
        resource_type="blog_post",
        resource_id=row.id,
        details={"slug": row.slug, "title": row.title, "published": row.published},
    )
    return _response(row)


@admin_router.get("/content/blog/{post_id}", response_model=models.BlogPostResponse)
async def get_blog_admin(post_id: str, admin_user: StaffUser):
    """Fetch one blog post (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(BlogPost).where(BlogPost.id == post_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return _response(row)


_BLOG_FIELD_MAP = {
    "slug": "slug",
    "title": "title",
    "description": "description",
    "content": "content",
    "authorName": "author_name",
    "authorRole": "author_role",
    "publishedAt": "published_at",
    "tags": "tags",
    "category": "category",
    "coverImage": "cover_image",
    "readTime": "read_time",
    "featured": "featured",
    "published": "published",
}


@admin_router.patch("/content/blog/{post_id}", response_model=models.BlogPostResponse)
async def update_blog_post(post_id: str, body: models.BlogPostUpdateRequest, admin_user: StaffUser):
    """Update a blog post (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    changes = body.model_dump(exclude_unset=True)
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(BlogPost).where(BlogPost.id == post_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Blog post not found")
        for key, value in changes.items():
            if key == "publishedAt" and value is not None:
                value = _naive(value)
            setattr(row, _BLOG_FIELD_MAP.get(key, key), value)
        row.updated_at = _naive_utc_now()
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="A post with that slug already exists")
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_blog_post",
        resource_type="blog_post",
        resource_id=post_id,
        details={"fields": sorted(changes.keys())},
    )
    return _response(row)


@admin_router.delete("/content/blog/{post_id}")
async def delete_blog_post(post_id: str, admin_user: StaffUser):
    """Delete a blog post (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(BlogPost).where(BlogPost.id == post_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Blog post not found")
        await session.delete(row)
        await session.commit()

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_blog_post",
        resource_type="blog_post",
        resource_id=post_id,
        details={"slug": row.slug},
    )
    return {"message": "Blog post deleted", "postId": post_id}


# ===========================================================================
# Public — published posts
# ===========================================================================


@public_router.get("", response_model=list[models.BlogPostResponse])
async def list_published_posts(
    category: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Published blog posts for the public site, newest first."""
    conditions = [BlogPost.published.is_(True)]
    if category:
        conditions.append(BlogPost.category == category)
    if search:
        conditions.append(ilike_any(search, BlogPost.title, BlogPost.description))

    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(BlogPost)
                    .where(*conditions)
                    .order_by(BlogPost.published_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [_response(r) for r in rows]


@public_router.get("/{slug}", response_model=models.BlogPostResponse)
async def get_published_post(slug: str):
    """One published blog post by slug."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BlogPost).where(BlogPost.slug == slug, BlogPost.published.is_(True))
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return _response(row)


# ===========================================================================
# Admin — content calendar (CRUD)
#
# The generate (LLM -> a `BlogPost`) and cover-image upload (object storage) actions the client also
# names are deliberately not built here: each is a subsystem of its own (the generation pipeline and
# BunnyCDN/object storage), out of proportion to this CRUD slice. See `docs/ADMIN_DASHBOARD_PLAN.md`.
# The admin client currently redirects its calendar page to the blog list, so nothing surfaces these
# yet regardless.
# ===========================================================================


def _calendar_response(
    row: ContentCalendarEntry,
) -> models.ContentCalendarEntryResponse:
    return models.ContentCalendarEntryResponse(
        id=row.id,
        topic=row.topic,
        keywords=row.keywords or [],
        category=row.category,
        scheduledDate=row.scheduled_date,
        status=row.status,
        blogPostId=row.blog_post_id,
        coverImageUrl=row.cover_image_url,
        notes=row.notes,
        autoPublish=row.auto_publish,
        errorMessage=row.error_message,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


@admin_router.get("/content/calendar", response_model=list[models.ContentCalendarEntryResponse])
async def list_calendar(admin_user: StaffUser):
    """All content-calendar entries, soonest scheduled first (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ContentCalendarEntry).order_by(ContentCalendarEntry.scheduled_date)
                )
            )
            .scalars()
            .all()
        )
    return [_calendar_response(r) for r in rows]


@admin_router.post(
    "/content/calendar",
    response_model=models.ContentCalendarEntryResponse,
    status_code=201,
)
async def create_calendar_entry(
    body: models.ContentCalendarEntryCreateRequest, admin_user: StaffUser
):
    """Plan a content-calendar entry (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    now = _naive_utc_now()
    row = ContentCalendarEntry(
        topic=body.topic,
        keywords=body.keywords,
        category=body.category if body.category is not None else "Study Tips",
        scheduled_date=_naive(body.scheduledDate),
        status="scheduled",
        auto_publish=body.autoPublish if body.autoPublish is not None else True,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="create_calendar_entry",
        resource_type="content_calendar_entry",
        resource_id=row.id,
        details={"topic": row.topic, "scheduledDate": row.scheduled_date.isoformat()},
    )
    return _calendar_response(row)


_CALENDAR_FIELD_MAP = {
    "topic": "topic",
    "keywords": "keywords",
    "category": "category",
    "scheduledDate": "scheduled_date",
    "status": "status",
    "autoPublish": "auto_publish",
    "notes": "notes",
    "coverImageUrl": "cover_image_url",
}


@admin_router.patch(
    "/content/calendar/{entry_id}", response_model=models.ContentCalendarEntryResponse
)
async def update_calendar_entry(
    entry_id: str, body: models.ContentCalendarEntryUpdateRequest, admin_user: StaffUser
):
    """Update a content-calendar entry (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    changes = body.model_dump(exclude_unset=True)
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(ContentCalendarEntry).where(ContentCalendarEntry.id == entry_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Calendar entry not found")
        for key, value in changes.items():
            if key == "scheduledDate" and value is not None:
                value = _naive(value)
            setattr(row, _CALENDAR_FIELD_MAP.get(key, key), value)
        row.updated_at = _naive_utc_now()
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_calendar_entry",
        resource_type="content_calendar_entry",
        resource_id=entry_id,
        details={"fields": sorted(changes.keys())},
    )
    return _calendar_response(row)


@admin_router.delete("/content/calendar/{entry_id}")
async def delete_calendar_entry(entry_id: str, admin_user: StaffUser):
    """Delete a content-calendar entry (staff only)."""
    from src.domains.admin.services.audit_service import log_admin_action

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(ContentCalendarEntry).where(ContentCalendarEntry.id == entry_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Calendar entry not found")
        await session.delete(row)
        await session.commit()

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_calendar_entry",
        resource_type="content_calendar_entry",
        resource_id=entry_id,
        details={"topic": row.topic},
    )
    return {"message": "Calendar entry deleted", "entryId": entry_id}
