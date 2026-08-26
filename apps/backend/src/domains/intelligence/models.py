"""
Intelligence domain — Pydantic request/response schemas.

Covers conversations (chat), messages, memory, and model preferences.

**Why every model here is a `CamelModel`.** Every response model in this file previously declared its
fields in camelCase on a plain `BaseModel` with `from_attributes=True`. The ORM maps camelCase
*columns* onto snake_case *attributes*, so validation looked for attributes that do not exist. Proven
against real rows before this rewrite:

    ConversationResponse.model_validate(<ChatSession id=sess_1 title=T>)
    3 validation errors: userId, createdAt, updatedAt — Field required
    ChatMessageResponse.model_validate(<ChatMessage id=msg_1 role=ASSISTANT>)
    3 validation errors: sessionId, userId, createdAt — Field required

The three that raise are the three without defaults, and they are the *lesser* half. The fields with
defaults did not raise: `courseId`, `topicId`, `examPrepId`, `noteId`, `spaceId`, `isActive`,
`sessionType` and `isSpaceRoom` would have been served as their declared default regardless of what
the row said, with a `200`. A conversation attached to a course would have been published as attached
to nothing. `CamelModel` closes both by construction — fields are snake_case, matching the ORM, and
the alias generator emits camelCase on the wire. See `src/shared/schemas.py`.

Guarded by `tests/test_intelligence_schemas.py`, which builds every model in this file from a real
ORM instance and asserts field-by-field against the row. That guard is the only thing that keeps this
class of defect fixed, because its failure mode is a `200`.
"""

from datetime import datetime

from pydantic import ConfigDict, Field

# Imported rather than defined here for the reason given in `src/shared/schemas.py`: more than one
# domain needs the same base and the same pagination envelope, and a copy per domain is how the two
# drift. Re-exported so `models.PaginatedResponse[...]` reads the same here as in the other domains.
from src.shared.schemas import CamelModel, CursorPage, PaginatedResponse

__all__ = [
    "CamelModel",
    "CursorPage",
    "PaginatedResponse",
    "ConversationCreate",
    "ConversationResponse",
    "MessageSend",
    "ChatMessageResponse",
    "UserFactResponse",
    "ConversationSummaryResponse",
    "MemoryContextResponse",
    "ModelPreferenceUpdate",
    "ModelPreferenceResponse",
]


# ===========================================================================
# Conversations (the ChatSession table)
# ===========================================================================


class ConversationCreate(CamelModel):
    """Start a new conversation."""

    title: str | None = None
    session_type: str = "general"  # "general", "onboarding"
    course_id: str | None = None
    topic_id: str | None = None
    exam_prep_id: str | None = None
    note_id: str | None = None
    space_id: str | None = None
    is_space_room: bool = False


class ConversationResponse(CamelModel):
    """A conversation session.

    Every field below is read off a `ChatSession` row. Nothing here has a default that could stand in
    for an unread attribute, except the two booleans and `session_type`, which are NOT NULL columns
    with server defaults and so are always present on a real row.
    """

    id: str
    user_id: str
    title: str | None = None
    is_active: bool
    session_type: str
    course_id: str | None = None
    topic_id: str | None = None
    exam_prep_id: str | None = None
    note_id: str | None = None
    space_id: str | None = None
    is_space_room: bool
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Messages
# ===========================================================================


class MessageSend(CamelModel):
    """Send a message in a conversation."""

    content: str = Field(..., min_length=1)
    image_urls: list[str] = Field(default_factory=list)
    audio_url: str | None = None
    reply_to_message_id: str | None = None


class ChatMessageResponse(CamelModel):
    """A single message in a conversation.

    Named `ChatMessageResponse`, not `MessageResponse`. Both `identity` and `personal_learning` already
    publish a `MessageResponse` — each a one-field generic acknowledgement — and FastAPI resolves a
    schema-name collision by fully qualifying *every* colliding name, so a third would have exported
    this as `src__domains__intelligence__models__MessageResponse` and generated a client type to match.
    It also happens to be the name web already hand-wrote for this shape. The other two collide with
    each other and did so before this router was mounted; untangling them is not this plan's work.

    `image_urls` is `list[str] | None` rather than `list[str] = []` because the column is a nullable
    array: a row with no images reads `None`, and declaring a list default would have rejected it
    outright. Rendering it as `[]` would also be the absent-as-zero mistake the plan's §1 forbids —
    though here the two are the same thing, the validation failure is not.

    `protected_namespaces=()` because `model_name` is a real column on this table and pydantic
    reserves the `model_` prefix. Renaming the field would put the ORM and the schema back out of
    step, which is the defect this file was rewritten to close.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: str
    session_id: str
    user_id: str
    role: str  # USER, ASSISTANT, SYSTEM
    content: str
    suggestion_text: str | None = None
    audio_url: str | None = None
    image_urls: list[str] | None = None
    component_data: dict | None = None
    token_count: int
    model_name: str | None = None
    reply_to_message_id: str | None = None
    created_at: datetime


# ===========================================================================
# Memory
# ===========================================================================


class UserFactResponse(CamelModel):
    """A learned fact about the user.

    Built by `memory_service.get_user_facts`, which maps the `UserFact` row's `content` onto `fact`
    and its `confidence` onto `importance`. The names differ from the column names deliberately and
    the mapping is the service's job, not this model's.
    """

    id: str
    fact: str
    category: str | None = None
    importance: float
    created_at: datetime


class ConversationSummaryResponse(CamelModel):
    """A summarized conversation for long-term memory."""

    id: str
    session_id: str
    summary: str
    key_topics: list[str] = Field(default_factory=list)
    created_at: datetime


class MemoryContextResponse(CamelModel):
    """What Intelligence remembers about a learner, for transparency and debugging.

    Previously declared `learningGoals`, `strengths` and `weaknesses` alongside these two, all
    defaulting to `[]`. Nothing in the repository produces them, so the endpoint would have published
    three empty lists as though the learner had no goals and no weaknesses rather than as though
    nothing had been measured. That is the absent-as-zero mistake, so the fields are gone rather than
    defaulted. They return when something computes them.
    """

    user_facts: list[UserFactResponse] = Field(default_factory=list)
    recent_summaries: list[ConversationSummaryResponse] = Field(default_factory=list)


# ===========================================================================
# Model preferences
# ===========================================================================


class ModelPreferenceUpdate(CamelModel):
    """Update user's preferred AI model for a capability."""

    model_config = ConfigDict(protected_namespaces=())

    capability: str = Field(..., description="chat, vision, structured_output, embedding")
    provider: str = Field(..., description="gemini, openai, anthropic")
    model_id: str = Field(..., description="e.g. gpt-4o-mini, claude-sonnet-4-20250514")


class ModelPreferenceResponse(CamelModel):
    """User's AI model preference."""

    model_config = ConfigDict(protected_namespaces=())

    capability: str
    provider: str
    model_id: str
