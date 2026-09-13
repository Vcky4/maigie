"""Careers domain — tables.

``JobPosting`` and ``CareerApplication`` already exist in the database (Prisma-era, currently empty),
with timezone-naive timestamps and Postgres ``text[]`` columns. **No migration is needed** — these
models mirror the live schema exactly (no ``TimestampMixin``; naive ``DateTime``; ``updatedAt`` has no
default and is set on write; indexes mirror the ones already present), the same approach ``AuditLog``
and ``Feedback`` take.

Neither table carries a ``userId``: an applicant is not necessarily a learner, and identity is the
email and name on the application itself.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base


class JobPosting(Base):
    """One open role, as shown on the public careers page and edited by staff."""

    __tablename__ = "JobPosting"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsibilities: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    requirements_must_have: Mapped[list[str] | None] = mapped_column(
        "requirementsMustHave", ARRAY(Text), nullable=True
    )
    requirements_nice_to_have: Mapped[list[str] | None] = mapped_column(
        "requirementsNiceToHave", ARRAY(Text), nullable=True
    )
    success_metrics: Mapped[list[str] | None] = mapped_column(
        "successMetrics", ARRAY(Text), nullable=True
    )
    why_role_matters: Mapped[list[str] | None] = mapped_column(
        "whyRoleMatters", ARRAY(Text), nullable=True
    )
    compensation: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    published: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    sort_order: Mapped[int] = mapped_column(
        "sortOrder", Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("JobPosting_slug_key", "slug", unique=True),
        Index("JobPosting_published_idx", "published"),
        Index("JobPosting_sortOrder_idx", "sortOrder"),
    )

    def __repr__(self) -> str:
        return f"<JobPosting id={self.id} slug={self.slug}>"


class CareerApplication(Base):
    """One application to one job. Applicant identity is the email/name here, not a learner account."""

    __tablename__ = "CareerApplication"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    #: The applied-for job. Plain string, mirroring the live column (no FK constraint in the table).
    #: ``jobTitle`` is snapshotted alongside so the application still reads if the posting is edited.
    job_id: Mapped[str] = mapped_column("jobId", Text, nullable=False)
    job_title: Mapped[str] = mapped_column("jobTitle", Text, nullable=False)
    first_name: Mapped[str] = mapped_column("firstName", Text, nullable=False)
    last_name: Mapped[str] = mapped_column("lastName", Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    linkedin_url: Mapped[str] = mapped_column("linkedinUrl", Text, nullable=False)
    portfolio_url: Mapped[str | None] = mapped_column("portfolioUrl", Text, nullable=True)
    cover_letter: Mapped[str] = mapped_column("coverLetter", Text, nullable=False)
    #: NEW | REVIEWED | ARCHIVED. No server default — mirrors the live column.
    status: Mapped[str] = mapped_column(String, nullable=False, default="NEW")
    admin_notes: Mapped[str | None] = mapped_column("adminNotes", Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column("userAgent", Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column("sourceIp", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("CareerApplication_createdAt_idx", "createdAt"),
        Index("CareerApplication_email_idx", "email"),
        Index("CareerApplication_jobId_idx", "jobId"),
        Index("CareerApplication_status_idx", "status"),
    )

    def __repr__(self) -> str:
        return f"<CareerApplication id={self.id} job={self.job_id} status={self.status}>"
