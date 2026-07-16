"""
Knowledge domain — SQLAlchemy models.

Course, Module, Topic, Resource, Embedding, CourseOutlineSatisfaction, UserTopicProgress.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Course(Base, TimestampMixin):
    __tablename__ = "Course"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    difficulty: Mapped[str] = mapped_column(String, default="BEGINNER", server_default="BEGINNER")
    target_date: Mapped[Optional[datetime]] = mapped_column("targetDate", DateTime(timezone=True), nullable=True)
    is_ai_generated: Mapped[bool] = mapped_column("isAIGenerated", Boolean, default=False, server_default="false")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)

    # Relationships
    modules: Mapped[list["Module"]] = relationship("Module", back_populates="course", lazy="selectin", order_by="Module.order")

    def __repr__(self) -> str:
        return f"<Course id={self.id} title={self.title}>"


class Module(Base, TimestampMixin):
    __tablename__ = "Module"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    course_id: Mapped[str] = mapped_column("courseId", String, ForeignKey("Course.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    order: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    course: Mapped["Course"] = relationship("Course", back_populates="modules")
    topics: Mapped[list["Topic"]] = relationship("Topic", back_populates="module", lazy="selectin", order_by="Topic.order")

    def __repr__(self) -> str:
        return f"<Module id={self.id} title={self.title}>"


class Topic(Base, TimestampMixin):
    __tablename__ = "Topic"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    module_id: Mapped[str] = mapped_column("moduleId", String, ForeignKey("Module.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    estimated_hours: Mapped[Optional[float]] = mapped_column("estimatedHours", Float, nullable=True)

    # Relationships
    module: Mapped["Module"] = relationship("Module", back_populates="topics")

    def __repr__(self) -> str:
        return f"<Topic id={self.id} title={self.title}>"


class UserTopicProgress(Base, TimestampMixin):
    """Per-user progress for shared Circle courses."""
    __tablename__ = "UserTopicProgress"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column("topicId", String, ForeignKey("Topic.id", ondelete="CASCADE"), index=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    completed_at: Mapped[Optional[datetime]] = mapped_column("completedAt", DateTime(timezone=True), nullable=True)
    minutes_spent: Mapped[float] = mapped_column("minutesSpent", Float, default=0, server_default="0")

    __table_args__ = (
        Index("UserTopicProgress_userId_topicId_key", "userId", "topicId", unique=True),
    )


class Resource(Base, TimestampMixin):
    __tablename__ = "Resource"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, default="OTHER", server_default="OTHER")
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    is_recommended: Mapped[bool] = mapped_column("isRecommended", Boolean, default=False, server_default="false")
    recommendation_score: Mapped[Optional[float]] = mapped_column("recommendationScore", Float, nullable=True)
    recommendation_source: Mapped[Optional[str]] = mapped_column("recommendationSource", String, nullable=True)
    recommendation_reason: Mapped[Optional[str]] = mapped_column("recommendationReason", String, nullable=True)
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)
    click_count: Mapped[int] = mapped_column("clickCount", Integer, default=0, server_default="0")
    bookmark_count: Mapped[int] = mapped_column("bookmarkCount", Integer, default=0, server_default="0")
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column("lastAccessedAt", DateTime(timezone=True), nullable=True)


class CourseOutlineSatisfaction(Base):
    """KPI tracking for AI-generated course outlines."""
    __tablename__ = "CourseOutlineSatisfaction"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str] = mapped_column("courseId", String, ForeignKey("Course.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    feedback: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))


class Embedding(Base, TimestampMixin):
    __tablename__ = "Embedding"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    object_type: Mapped[str] = mapped_column("objectType", String, nullable=False)
    object_id: Mapped[str] = mapped_column("objectId", String, nullable=False)
    vector: Mapped[dict] = mapped_column(JSON, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column("resourceId", String, nullable=True, index=True)
    resource_bank_item_id: Mapped[Optional[str]] = mapped_column("resourceBankItemId", String, nullable=True, index=True)

    __table_args__ = (
        Index("Embedding_objectType_objectId_idx", "objectType", "objectId"),
    )
