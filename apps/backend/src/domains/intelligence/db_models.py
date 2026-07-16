"""
Intelligence domain — SQLAlchemy models.

ChatSession, ChatMessage, AIActionLog, LlmCostRecord,
UserInteractionMemory, UserFact, ConversationSummary,
AIAgentTask, LearningInsight, UserUpload.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, Numeric, String, Text,
    ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------


class ChatSession(Base, TimestampMixin):
    __tablename__ = "ChatSession"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, server_default="true")
    is_space_room: Mapped[bool] = mapped_column("isSpaceRoom", Boolean, default=False, server_default="false")
    session_type: Mapped[str] = mapped_column("sessionType", String, default="general", server_default="general")

    # Context links
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    exam_prep_id: Mapped[Optional[str]] = mapped_column("examPrepId", String, nullable=True, index=True)
    note_id: Mapped[Optional[str]] = mapped_column("noteId", String, nullable=True, index=True)

    # Relationships
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="session", lazy="noload")
    conversation_summaries: Mapped[list["ConversationSummary"]] = relationship("ConversationSummary", back_populates="session", lazy="noload")

    __table_args__ = (
        Index("ChatSession_isSpaceRoom_idx", "isSpaceRoom"),
        Index("ChatSession_sessionType_idx", "sessionType"),
        Index("ChatSession_userId_spaceId_courseId_idx", "userId", "spaceId", "courseId"),
        Index("ChatSession_userId_spaceId_topicId_idx", "userId", "spaceId", "topicId"),
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# ChatMessage
# ---------------------------------------------------------------------------


class ChatMessage(Base):
    __tablename__ = "ChatMessage"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    session_id: Mapped[str] = mapped_column("sessionId", String, ForeignKey("ChatSession.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    # Review threading
    review_item_id: Mapped[Optional[str]] = mapped_column("reviewItemId", String, nullable=True, index=True)

    # Reply threading
    reply_to_message_id: Mapped[Optional[str]] = mapped_column("replyToMessageId", String, ForeignKey("ChatMessage.id", ondelete="SET NULL"), nullable=True, index=True)

    role: Mapped[str] = mapped_column(String, nullable=False)  # USER, ASSISTANT, SYSTEM
    content: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_text: Mapped[Optional[str]] = mapped_column("suggestionText", String, nullable=True)

    # Voice/media
    audio_url: Mapped[Optional[str]] = mapped_column("audioUrl", String, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column("imageUrl", String, nullable=True)
    image_urls: Mapped[Optional[list]] = mapped_column("imageUrls", ARRAY(String), nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Component data
    component_data: Mapped[Optional[dict]] = mapped_column("componentData", JSON, nullable=True)

    # Token/cost tracking
    token_count: Mapped[int] = mapped_column("tokenCount", Integer, default=0, server_default="0")
    input_tokens: Mapped[Optional[int]] = mapped_column("inputTokens", Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column("outputTokens", Integer, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column("modelName", String, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column("costUsd", Float, nullable=True)
    revenue_usd: Mapped[Optional[float]] = mapped_column("revenueUsd", Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
    actions: Mapped[list["AIActionLog"]] = relationship("AIActionLog", back_populates="message", lazy="noload")

    __table_args__ = (
        Index("ChatMessage_sessionId_reviewItemId_idx", "sessionId", "reviewItemId"),
        Index("ChatMessage_createdAt_idx", "createdAt"),
    )

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} role={self.role}>"


# ---------------------------------------------------------------------------
# AIActionLog
# ---------------------------------------------------------------------------


class AIActionLog(Base):
    __tablename__ = "AIActionLog"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    message_id: Mapped[str] = mapped_column("messageId", String, ForeignKey("ChatMessage.id", ondelete="CASCADE"), index=True)

    action_type: Mapped[str] = mapped_column("actionType", String, nullable=False)
    action_data: Mapped[dict] = mapped_column("actionData", JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, default="PENDING", server_default="PENDING")
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    message: Mapped["ChatMessage"] = relationship("ChatMessage", back_populates="actions")


# ---------------------------------------------------------------------------
# LlmCostRecord
# ---------------------------------------------------------------------------


class LlmCostRecord(Base):
    __tablename__ = "LlmCostRecord"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, index=True)
    user_tier: Mapped[str] = mapped_column("userTier", String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column("inputTokens", Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column("outputTokens", Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column("costUsd", Numeric(12, 6), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("LlmCostRecord_provider_model_idx", "provider", "model"),
        Index("LlmCostRecord_createdAt_idx", "createdAt"),
        Index("LlmCostRecord_userId_createdAt_idx", "userId", "createdAt"),
    )


# ---------------------------------------------------------------------------
# UserInteractionMemory
# ---------------------------------------------------------------------------


class UserInteractionMemory(Base):
    __tablename__ = "UserInteractionMemory"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    interaction_type: Mapped[str] = mapped_column("interactionType", String, nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String, nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column("entityId", String, nullable=True)

    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("UserInteractionMemory_userId_interactionType_idx", "userId", "interactionType"),
        Index("UserInteractionMemory_userId_createdAt_idx", "userId", "createdAt"),
        Index("UserInteractionMemory_entityType_entityId_idx", "entityType", "entityId"),
    )


# ---------------------------------------------------------------------------
# UserFact
# ---------------------------------------------------------------------------


class UserFact(Base, TimestampMixin):
    __tablename__ = "UserFact"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    category: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, default="conversation", server_default="conversation")

    confidence: Mapped[float] = mapped_column(Float, default=0.8, server_default="0.8")
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, server_default="true")

    __table_args__ = (
        Index("UserFact_userId_category_idx", "userId", "category"),
        Index("UserFact_userId_isActive_idx", "userId", "isActive"),
    )


# ---------------------------------------------------------------------------
# ConversationSummary
# ---------------------------------------------------------------------------


class ConversationSummary(Base):
    __tablename__ = "ConversationSummary"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column("sessionId", String, ForeignKey("ChatSession.id", ondelete="CASCADE"), index=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_topics: Mapped[Optional[list]] = mapped_column("keyTopics", ARRAY(String), nullable=True)
    actions_taken: Mapped[Optional[list]] = mapped_column("actionsTaken", ARRAY(String), nullable=True)
    emotional_tone: Mapped[Optional[str]] = mapped_column("emotionalTone", String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="conversation_summaries")

    __table_args__ = (
        Index("ConversationSummary_userId_createdAt_idx", "userId", "createdAt"),
    )


# ---------------------------------------------------------------------------
# AIAgentTask
# ---------------------------------------------------------------------------


class AIAgentTask(Base, TimestampMixin):
    __tablename__ = "AIAgentTask"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    task_type: Mapped[str] = mapped_column("taskType", String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    action_data: Mapped[Optional[dict]] = mapped_column("actionData", JSON, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column("scheduledAt", DateTime(timezone=True), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column("sentAt", DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column("dismissedAt", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("AIAgentTask_userId_status_idx", "userId", "status"),
        Index("AIAgentTask_scheduledAt_status_idx", "scheduledAt", "status"),
    )


# ---------------------------------------------------------------------------
# LearningInsight
# ---------------------------------------------------------------------------


class LearningInsight(Base, TimestampMixin):
    __tablename__ = "LearningInsight"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    insight_type: Mapped[str] = mapped_column("insightType", String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    data_points: Mapped[int] = mapped_column("dataPoints", Integer, default=1, server_default="1")
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, server_default="true")

    __table_args__ = (
        Index("LearningInsight_userId_insightType_idx", "userId", "insightType"),
        Index("LearningInsight_userId_isActive_idx", "userId", "isActive"),
    )


# ---------------------------------------------------------------------------
# UserUpload
# ---------------------------------------------------------------------------


class UserUpload(Base):
    __tablename__ = "UserUpload"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    url: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column("mimeType", String, nullable=True)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column("extractedText", Text, nullable=True)
    embedding_id: Mapped[Optional[str]] = mapped_column("embeddingId", String, nullable=True)
    chat_message_id: Mapped[Optional[str]] = mapped_column("chatMessageId", String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("UserUpload_userId_createdAt_idx", "userId", "createdAt"),
    )
