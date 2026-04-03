"""
Public read-only blog API for the marketing site.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from src.dependencies import DBDep

router = APIRouter(prefix="/api/v1/blog", tags=["blog"])


def _serialize_published_at(dt: datetime) -> str:
    return dt.date().isoformat()


def _post_to_marketing_dict(row) -> dict:
    return {
        "slug": row.slug,
        "title": row.title,
        "description": row.description,
        "content": row.content,
        "author": {"name": row.authorName, "role": row.authorRole},
        "publishedAt": _serialize_published_at(row.publishedAt),
        "tags": row.tags,
        "category": row.category,
        "coverImage": row.coverImage,
        "readTime": row.readTime,
        "featured": row.featured,
    }


@router.get("/posts")
async def list_published_posts(db: DBDep):
    rows = await db.blogpost.find_many(
        where={"published": True},
        order={"publishedAt": "desc"},
    )
    return [_post_to_marketing_dict(r) for r in rows]


@router.get("/posts/{slug}")
async def get_published_post(slug: str, db: DBDep):
    row = await db.blogpost.find_first(where={"slug": slug, "published": True})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return _post_to_marketing_dict(row)
