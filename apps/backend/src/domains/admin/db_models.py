"""Admin domain tables.

The ``AuditLog`` table already exists in the database with the shape below; it was
created before the move to SQLAlchemy and no model was written for it during the
migration, which is why ``audit_service`` had nowhere to write and the table holds zero
rows. Adding this model needs no migration.

Column names are the camelCase originals, mapped to snake_case attributes to match the
convention used by the other domain models.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base


class AuditLog(Base):
    """An immutable record of a privileged action."""

    __tablename__ = "AuditLog"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    # The live constraint is ON UPDATE CASCADE ON DELETE SET NULL even though the column
    # is NOT NULL, so deleting an administrator would violate it. That inconsistency
    # predates this model and is mirrored here rather than silently corrected, since
    # changing it needs a migration.
    admin_user_id: Mapped[str] = mapped_column(
        "adminUserId",
        String,
        ForeignKey("User.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column("actionType", Text, nullable=False)
    resource_type: Mapped[str] = mapped_column("resourceType", Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column("resourceId", Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    # Mirrors the indexes that exist in the database, so an autogenerate diff stays
    # empty rather than proposing to create or drop any of them.
    __table_args__ = (
        Index("AuditLog_actionType_idx", "actionType"),
        Index("AuditLog_adminUserId_idx", "adminUserId"),
        Index("AuditLog_resourceType_idx", "resourceType"),
        Index("AuditLog_resourceType_resourceId_idx", "resourceType", "resourceId"),
        Index("AuditLog_timestamp_idx", "timestamp"),
    )
