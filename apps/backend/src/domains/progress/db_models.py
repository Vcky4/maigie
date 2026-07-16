"""
Progress domain — SQLAlchemy models.

Goal, ScheduleBlock, StudySession, UserStreak, Achievement,
ReviewItem, ScheduleBehaviourLog.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------


class Goal(Base, TimestampMixin):
    __tablename__ = "Goal"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_date: Mapped[Optional[datetime]] = mapped_column("targetDate", DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE", server_default="ACTIVE")
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # Optional links
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)

    # Relationships
    schedules: Mapped[list["ScheduleBlock"]] = relationship(
        "ScheduleBlock", back_populates="goal", lazy="selectin"
    )

    __table_args__ = (
        Index("Goal_userId_status_idx", "userId", "status"),
        Index("Goal_targetDate_idx", "targetDate"),
    )

    def __repr__(self) -> str:
        return f"<Goal id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# ScheduleBlock
# ---------------------------------------------------------------------------


class ScheduleBlock(Base, TimestampMixin):
    __tablename__ = "ScheduleBlock"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    start_at: Mapped[datetime] = mapped_column("startAt", DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column("endAt", DateTime(timezone=True), nullable=False)
    recurring_rule: Mapped[Optional[str]] = mapped_column("recurringRule", String, nullable=True)

    # Google Calendar sync
    google_calendar_event_id: Mapped[Optional[str]] = mapped_column("googleCalendarEventId", String, nullable=True)
    google_calendar_synced_at: Mapped[Optional[datetime]] = mapped_column("googleCalendarSyncedAt", DateTime(timezone=True), nullable=True)

    # Optional links
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    goal_id: Mapped[Optional[str]] = mapped_column("goalId", String, ForeignKey("Goal.id", ondelete="SET NULL"), nullable=True, index=True)

    # Spaced repetition link
    review_item_id: Mapped[Optional[str]] = mapped_column("reviewItemId", String, ForeignKey("ReviewItem.id", ondelete="SET NULL"), unique=True, nullable=True)

    # Exam prep link
    exam_prep_id: Mapped[Optional[str]] = mapped_column("examPrepId", String, nullable=True, index=True)

    # Relationships
    goal: Mapped[Optional["Goal"]] = relationship("Goal", back_populates="schedules")
    review_item: Mapped[Optional["ReviewItem"]] = relationship("ReviewItem", back_populates="schedule_block")

    __table_args__ = (
        Index("ScheduleBlock_userId_startAt_idx", "userId", "startAt"),
        Index("ScheduleBlock_startAt_endAt_idx", "startAt", "endAt"),
    )

    def __repr__(self) -> str:
        return f"<ScheduleBlock id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# ReviewItem (Spaced Repetition)
# ---------------------------------------------------------------------------


class ReviewItem(Base, TimestampMixin):
    __tablename__ = "ReviewItem"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column("topicId", String, ForeignKey("Topic.id", ondelete="CASCADE"), index=True)

    # SM-2 fields
    next_review_at: Mapped[datetime] = mapped_column("nextReviewAt", DateTime(timezone=True), nullable=False)
    interval_days: Mapped[int] = mapped_column("intervalDays", Integer, default=1, server_default="1")
    repetition_count: Mapped[int] = mapped_column("repetitionCount", Integer, default=0, server_default="0")
    ease_factor: Mapped[float] = mapped_column("easeFactor", Float, default=2.5, server_default="2.5")
    last_quality: Mapped[int] = mapped_column("lastQuality", Integer, default=-1, server_default="-1")
    lapse_count: Mapped[int] = mapped_column("lapseCount", Integer, default=0, server_default="0")
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column("lastReviewedAt", DateTime(timezone=True), nullable=True)

    # Relationships
    topic: Mapped[Optional["Topic"]] = relationship("Topic", lazy="selectin")
    schedule_block: Mapped[Optional["ScheduleBlock"]] = relationship("ScheduleBlock", back_populates="review_item", uselist=False)

    __table_args__ = (
        Index("ReviewItem_userId_nextReviewAt_idx", "userId", "nextReviewAt"),
    )

    def __repr__(self) -> str:
        return f"<ReviewItem id={self.id} topicId={self.topic_id}>"


# ---------------------------------------------------------------------------
# ScheduleBehaviourLog
# ---------------------------------------------------------------------------


class ScheduleBehaviourLog(Base):
    __tablename__ = "ScheduleBehaviourLog"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    behaviour_type: Mapped[str] = mapped_column("behaviourType", String, nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String, nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column("entityId", String, nullable=True)

    scheduled_at: Mapped[Optional[datetime]] = mapped_column("scheduledAt", DateTime(timezone=True), nullable=True)
    actual_at: Mapped[Optional[datetime]] = mapped_column("actualAt", DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("ScheduleBehaviourLog_userId_createdAt_idx", "userId", "createdAt"),
        Index("ScheduleBehaviourLog_behaviourType_idx", "behaviourType"),
    )


# ---------------------------------------------------------------------------
# StudySession
# ---------------------------------------------------------------------------


class StudySession(Base, TimestampMixin):
    __tablename__ = "StudySession"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    start_time: Mapped[datetime] = mapped_column("startTime", DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column("endTime", DateTime(timezone=True), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # Context
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    __table_args__ = (
        Index("StudySession_userId_startTime_idx", "userId", "startTime"),
        Index("StudySession_startTime_idx", "startTime"),
    )

    def __repr__(self) -> str:
        return f"<StudySession id={self.id} userId={self.user_id}>"


# ---------------------------------------------------------------------------
# UserStreak
# ---------------------------------------------------------------------------


class UserStreak(Base, TimestampMixin):
    __tablename__ = "UserStreak"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), unique=True, index=True)

    current_streak: Mapped[int] = mapped_column("currentStreak", Integer, default=0, server_default="0")
    longest_streak: Mapped[int] = mapped_column("longestStreak", Integer, default=0, server_default="0")
    last_study_date: Mapped[Optional[datetime]] = mapped_column("lastStudyDate", DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<UserStreak userId={self.user_id} current={self.current_streak}>"


# ---------------------------------------------------------------------------
# Achievement
# ---------------------------------------------------------------------------


class Achievement(Base):
    __tablename__ = "Achievement"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    achievement_type: Mapped[str] = mapped_column("achievementType", String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    unlocked_at: Mapped[datetime] = mapped_column(
        "unlockedAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("Achievement_userId_achievementType_idx", "userId", "achievementType"),
    )

    def __repr__(self) -> str:
        return f"<Achievement id={self.id} type={self.achievement_type}>"


# Import Topic for relationship resolution (avoid circular at module level)
from src.domains.knowledge.db_models import Topic  # noqa: E402, F401
