"""Content domain — tables.

``BlogPost`` already exists in the database (Prisma-era, currently empty), with timezone-naive
timestamps and a ``text[]`` tags column. **No migration is needed** — this model mirrors the live
schema exactly (no ``TimestampMixin``; naive ``DateTime``; ``publishedAt``/``updatedAt`` have no
default and are set on write; indexes mirror the live ones), the same approach ``Feedback`` and the
careers tables take.

Marketing content is owned by the backend (plan Decision 7); the public site reads published posts
from here.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base


class ContentCalendarEntry(Base):
    """A planned blog topic and its schedule. Mirrors the existing (Prisma-era) table — no migration.

    Timestamps are timezone-naive and ``updatedAt`` has no default (set on write), the same shape as
    ``BlogPost``. ``blogPostId`` links to the post produced from this entry once one exists.
    """

    __tablename__ = "ContentCalendarEntry"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    category: Mapped[str] = mapped_column(
        Text, nullable=False, default="Study Tips", server_default="Study Tips"
    )
    scheduled_date: Mapped[datetime] = mapped_column("scheduledDate", DateTime, nullable=False)
    #: scheduled | generating | generated | published | failed (free string, as the DB stores).
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="scheduled", server_default="scheduled"
    )
    blog_post_id: Mapped[str | None] = mapped_column("blogPostId", Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column("coverImageUrl", Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_publish: Mapped[bool] = mapped_column(
        "autoPublish", Boolean, nullable=False, default=True, server_default="true"
    )
    error_message: Mapped[str | None] = mapped_column("errorMessage", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("ContentCalendarEntry_scheduledDate_idx", "scheduledDate"),
        Index("ContentCalendarEntry_status_scheduledDate_idx", "status", "scheduledDate"),
    )

    def __repr__(self) -> str:
        return f"<ContentCalendarEntry id={self.id} topic={self.topic!r} status={self.status}>"


class BlogPost(Base):
    """One blog article, edited by staff and shown on the public site when published."""

    __tablename__ = "BlogPost"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column("authorName", Text, nullable=False)
    author_role: Mapped[str | None] = mapped_column("authorRole", Text, nullable=True)
    #: Naive (no tz), mirroring the live column. No default — set on write.
    published_at: Mapped[datetime] = mapped_column("publishedAt", DateTime, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    cover_image: Mapped[str | None] = mapped_column("coverImage", Text, nullable=True)
    read_time: Mapped[int | None] = mapped_column("readTime", Integer, nullable=True)
    featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("BlogPost_slug_key", "slug", unique=True),
        Index("BlogPost_published_idx", "published"),
        Index("BlogPost_publishedAt_idx", "publishedAt"),
    )

    def __repr__(self) -> str:
        return f"<BlogPost id={self.id} slug={self.slug} published={self.published}>"
