"""
Intelligence domain — Pydantic request/response schemas.

Covers conversations (chat), messages, memory, voice, and recommendations.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Conversations (was ChatSession)
# ===========================================================================


class ConversationCreate(BaseModel):
    """Start a new conversation."""

    title: str | None = None
    sessionType: str = "general"  # "general", "onboarding"
    courseId: str | None = None
    topicId: str | None = None
    examPrepId: str | None = None
    noteId: str | None = None
    circleId: str | None = None
    isCircleRoom: bool = False


class ConversationResponse(BaseModel):
    """A conversation session."""

    id: str
    userId: str
    title: str | None = None
    isActive: bool = True
    sessionType: str = "general"
    courseId: str | None = None
    topicId: str | None = None
    examPrepId: str | None = None
    noteId: str | None = None
    circleId: str | None = None
    isCircleRoom: bool = False
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    """Paginated conversation list."""

    conversations: list[ConversationResponse]
    total: int
    page: int
    pageSize: int


# ===========================================================================
# Messages
# ===========================================================================


class MessageSend(BaseModel):
    """Send a message in a conversation."""

    content: str = Field(..., min_length=1)
    imageUrls: list[str] = []
    audioUrl: str | None = None
    replyToMessageId: str | None = None


class MessageResponse(BaseModel):
    """A single message in a conversation."""

    id: str
    sessionId: str
    userId: str
    role: str  # USER, ASSISTANT, SYSTEM
    content: str
    suggestionText: str | None = None
    audioUrl: str | None = None
    imageUrls: list[str] = []
    componentData: Any = None
    tokenCount: int = 0
    modelName: str | None = None
    replyToMessageId: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageListResponse(BaseModel):
    """Paginated message list."""

    messages: list[MessageResponse]
    total: int


# ===========================================================================
# Chat (HTTP endpoint, non-streaming)
# ===========================================================================


class ChatRequest(BaseModel):
    """Send a chat message and get AI response."""

    message: str = Field(..., min_length=1)
    sessionId: str | None = None  # Existing conversation or create new
    courseId: str | None = None
    topicId: str | None = None
    imageUrls: list[str] = []


class ChatResponse(BaseModel):
    """AI chat response."""

    sessionId: str
    message: MessageResponse
    actions: list[dict] = []


# ===========================================================================
# Memory
# ===========================================================================


class UserFactResponse(BaseModel):
    """A learned fact about the user."""

    id: str
    fact: str
    category: str | None = None
    importance: float = 0.5
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationSummaryResponse(BaseModel):
    """A summarized conversation for long-term memory."""

    id: str
    sessionId: str
    summary: str
    keyTopics: list[str] = []
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryContextResponse(BaseModel):
    """The full memory context for a user (used internally by reasoning)."""

    userFacts: list[UserFactResponse] = []
    recentSummaries: list[ConversationSummaryResponse] = []
    learningGoals: list[str] = []
    strengths: list[str] = []
    weaknesses: list[str] = []


# ===========================================================================
# Voice
# ===========================================================================


class VoiceSessionStartRequest(BaseModel):
    """Start a voice conversation session."""

    sessionId: str | None = None
    greeting: str | None = None


class VoiceSessionResponse(BaseModel):
    """Voice session info."""

    sessionId: str
    status: str  # "active", "ended"
    creditsUsed: int = 0


# ===========================================================================
# Recommendations
# ===========================================================================


class RecommendationResponse(BaseModel):
    """A proactive recommendation from Intelligence."""

    type: str  # "revision", "collaboration", "resource", "session"
    title: str
    description: str
    actionUrl: str | None = None
    priority: float = 0.5
    metadata: dict = {}


# ===========================================================================
# Model Selection (user preferences)
# ===========================================================================


class ModelPreferenceUpdate(BaseModel):
    """Update user's preferred AI model for a capability."""

    capability: str = Field(..., description="chat, vision, structured_output, embedding")
    provider: str = Field(..., description="gemini, openai, anthropic")
    modelId: str = Field(..., description="e.g. gpt-4o-mini, claude-sonnet-4-20250514")


class ModelPreferenceResponse(BaseModel):
    """User's AI model preference."""

    capability: str
    provider: str
    modelId: str

    model_config = ConfigDict(from_attributes=True)
