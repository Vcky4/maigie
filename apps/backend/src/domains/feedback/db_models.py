"""Feedback domain — table.

The ``Feedback`` table already exists in the database with the shape below; it was created before the
move to SQLAlchemy (Prisma-era) and no model was written for it during the migration, which is why the
admin client's Feedback pages had nothing to call. It already holds real rows. **Adding this model
needs no migration** — the same situation as ``AuditLog``.

Two things are mirrored deliberately rather than "corrected", because changing them needs a migration:
the timestamps are ``timestamp without time zone`` (so this does **not** use ``TimestampMixin``, whose
columns are tz-aware), and ``status`` carries no server default. Column names are the camelCase
originals mapped to snake_case attributes, and the indexes mirror the ones already in the database so
an autogenerate diff stays empty.

``userId`` is ``ON DELETE SET NULL``: a bug report keeps its value after the person who filed it is
gone, and forgetting the learner should not erase the finding. Nullable, so feedback can be submitted
without an account.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base


class Feedback(Base):
    """A single piece of learner feedback. Mirrors the existing (Prisma-era) table."""

    __tablename__ = "Feedback"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str | None] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    #: BUG_REPORT | FEATURE_REQUEST | GENERAL_FEEDBACK | UI_UX_FEEDBACK | PERFORMANCE_ISSUE | OTHER.
    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: PENDING | REVIEWED | RESOLVED | ARCHIVED. No server default — mirrors the live column.
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    page_url: Mapped[str | None] = mapped_column("pageUrl", Text, nullable=True)
    #: The submitting client's user-agent, if reported. Present in the live table.
    user_agent: Mapped[str | None] = mapped_column("userAgent", Text, nullable=True)
    #: Client-supplied context. Named ``metadata_json`` because ``metadata`` is reserved on the
    #: declarative base; the column itself is ``metadata``.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    #: Staff-only triage note. Never shown to the learner.
    admin_notes: Mapped[str | None] = mapped_column("adminNotes", Text, nullable=True)
    #: Naive (no tz), mirroring the live column.
    resolved_at: Mapped[datetime | None] = mapped_column("resolvedAt", DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("Feedback_createdAt_idx", "createdAt"),
        Index("Feedback_status_idx", "status"),
        Index("Feedback_type_idx", "type"),
        Index("Feedback_userId_idx", "userId"),
    )

    def __repr__(self) -> str:
        return f"<Feedback id={self.id} type={self.type} status={self.status}>"
