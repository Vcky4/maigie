"""
Personal Learning domain — SQLAlchemy models.

Note, NoteTag, NoteAttachment, NoteHistory, ExamPrep, GeneratedDocument.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


class Note(Base, TimestampMixin):
    __tablename__ = "Note"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, ForeignKey("Course.id", ondelete="SET NULL"), nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, ForeignKey("Topic.id", ondelete="SET NULL"), nullable=True, index=True)
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="SET NULL"), nullable=True, index=True)

    last_edited_by_id: Mapped[Optional[str]] = mapped_column("lastEditedById", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    voice_recording_url: Mapped[Optional[str]] = mapped_column("voiceRecordingUrl", String, nullable=True)

    # Relationships
    tags: Mapped[list["NoteTag"]] = relationship("NoteTag", back_populates="note", cascade="all, delete-orphan", lazy="selectin")
    attachments: Mapped[list["NoteAttachment"]] = relationship("NoteAttachment", back_populates="note", cascade="all, delete-orphan", lazy="selectin")
    history: Mapped[list["NoteHistory"]] = relationship("NoteHistory", back_populates="note", cascade="all, delete-orphan", lazy="noload")

    __table_args__ = (
        Index("Note_userId_archived_idx", "userId", "archived"),
    )

    def __repr__(self) -> str:
        return f"<Note id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# NoteTag
# ---------------------------------------------------------------------------


class NoteTag(Base):
    __tablename__ = "NoteTag"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    note_id: Mapped[str] = mapped_column("noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="tags")

    def __repr__(self) -> str:
        return f"<NoteTag id={self.id} tag={self.tag}>"


# ---------------------------------------------------------------------------
# NoteAttachment
# ---------------------------------------------------------------------------


class NoteAttachment(Base):
    __tablename__ = "NoteAttachment"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    note_id: Mapped[str] = mapped_column("noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="attachments")

    def __repr__(self) -> str:
        return f"<NoteAttachment id={self.id} filename={self.filename}>"


# ---------------------------------------------------------------------------
# NoteHistory
# ---------------------------------------------------------------------------


class NoteHistory(Base):
    __tablename__ = "NoteHistory"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    note_id: Mapped[str] = mapped_column("noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="history")

    def __repr__(self) -> str:
        return f"<NoteHistory id={self.id} noteId={self.note_id}>"


# ---------------------------------------------------------------------------
# ExamPrep
# ---------------------------------------------------------------------------


class ExamPrep(Base, TimestampMixin):
    __tablename__ = "ExamPrep"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    subject: Mapped[str] = mapped_column(String, nullable=False)
    exam_date: Mapped[datetime] = mapped_column("examDate", DateTime(timezone=True), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="SETUP", server_default="SETUP")

    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        Index("ExamPrep_userId_examDate_idx", "userId", "examDate"),
        Index("ExamPrep_userId_status_idx", "userId", "status"),
        Index("ExamPrep_examDate_idx", "examDate"),
    )

    def __repr__(self) -> str:
        return f"<ExamPrep id={self.id} subject={self.subject}>"


# ---------------------------------------------------------------------------
# GeneratedDocument
# ---------------------------------------------------------------------------


class GeneratedDocument(Base, TimestampMixin):
    __tablename__ = "GeneratedDocument"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)
    style: Mapped[str] = mapped_column(String, default="academic", server_default="academic")
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_url: Mapped[str] = mapped_column("fileUrl", String, nullable=False)
    preview_url: Mapped[str] = mapped_column("previewUrl", String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    content_type: Mapped[str] = mapped_column("contentType", String, nullable=False)

    # Share settings
    is_public: Mapped[bool] = mapped_column("isPublic", Boolean, default=False, server_default="false")
    share_id: Mapped[Optional[str]] = mapped_column("shareId", String, unique=True, nullable=True)

    def __repr__(self) -> str:
        return f"<GeneratedDocument id={self.id} title={self.title}>"
