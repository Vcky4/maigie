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
    "GenerationAttemptSummary",
    "UserFactResponse",
    "ConversationSummaryResponse",
    "MemoryContextResponse",
    "ModelPreferenceUpdate",
    "ModelPreferenceResponse",
    "AskRequest",
    "AskResponse",
    "AskSkillBadge",
    "AskScope",
    "AskAction",
    "AskAttachmentResponse",
]


class AskAttachmentResponse(CamelModel):
    """A stored attachment, ready to be sent with a turn.

    `url` is what goes into `AskRequest.image_urls` and `ChatMessage.image_urls`. `id` is what a
    `DELETE` addresses — returned because without it an attachment the learner removes from the composer
    can only be orphaned, which is §6.1's reason for wanting a delete route at all.
    """

    id: str
    url: str
    filename: str
    mime_type: str | None = None
    size: int | None = None


# ===========================================================================
# Ask — one turn over HTTP
# ===========================================================================


class AskRequest(CamelModel):
    """One turn.

    `session_id` is optional: absent starts a new conversation, present appends to that one. The server
    resolves and authorises it either way — an id here is a claim, not a permission (see
    `ask_service.resolve_session_for_turn`).

    `context` is the page scope the socket already sends under the same name: `courseId`, `topicId`,
    `noteId`, `reviewItemId` and the pasted `content` / `noteContent`. Deliberately a free-form dict
    rather than a modelled shape, and that is a compromise worth naming: enrichment reads keys that
    accumulated over time, so modelling it now would either be wrong or would freeze a shape that is
    still moving. `ask_service.AskContext.from_client` is where it is read, and the four ids that matter
    are named there.

    `message` carries no `max_length` here on purpose. The limit is
    `ask_service.MESSAGE_MAX_LENGTH` and it is enforced by `screen_turn`, so that both transports refuse
    the same message with the same words — a pydantic `422` on this route and a friendlier refusal on the
    socket would be two different contracts for one rule. `min_length=1` is kept because it costs
    nothing and rejects the most obvious case at the edge.
    """

    message: str = Field(..., min_length=1)
    session_id: str | None = None
    context: dict | None = None
    image_urls: list[str] = Field(default_factory=list)


class AskSkillBadge(CamelModel):
    """A capability Maigie used on this turn, for the "what did it just do" affordance."""

    id: str
    name: str
    icon: str


class AskScope(CamelModel):
    """What the answer was allowed to draw on — Decision G's honesty requirement.

    **`library_recall` is the load-bearing field and it is `False` today.** Retrieval v1 is budgeted
    excerpting over what the client put in scope, not a search of everything the learner has written. An
    answer from one topic's notes and an answer from a whole library are different claims, and a client
    that renders them identically is asserting the stronger one — so "I could not find anything about
    that" must not be shown as a statement about the library while this is false.

    `sources` is a list of names rather than a score, because the only claim that can be made without
    inventing a measurement is "these are the things I looked at".
    """

    sources: list[str] = Field(default_factory=list)
    library_recall: bool = False


class AskAction(CamelModel):
    """Something Maigie actually did on this turn.

    **Only from the model's own tool output** (Decision I). The surface previously published a
    `suggestedAction` produced by keyword-matching the learner's own words and presented as the model's
    recommendation — which §1's second clause forbids outright, because it is a claim that is false. These
    are the executed actions, so a client offering a follow-up is offering one tied to something real.

    `status` is carried rather than filtered so a failed action is visible. The event frames and the
    components are both success-shaped, so a turn whose tool failed otherwise looks like a turn that used
    no tools.
    """

    type: str
    status: str
    course_id: str | None = None


class AskResponse(CamelModel):
    """What one turn produced.

    The same values the socket sends across several frames, collapsed into one body — which is what makes
    the two transports comparable rather than merely coexistent. `content` is the answer,
    `suggestion_text` the trailing suggestion when there was one, `components` the rich blocks, and
    `skills_used` the badges.

    **`session_id` is always returned, including when the request did not send one.** A client starting a
    conversation needs the id back to continue it, and making them read it out of the message row would
    be a second contract for the same fact.
    """

    id: str
    attempt_id: str | None = None
    session_id: str
    content: str
    suggestion_text: str | None = None
    components: list[dict] = Field(default_factory=list)
    skills_used: list[AskSkillBadge] = Field(default_factory=list)
    scope: AskScope = Field(default_factory=AskScope)
    actions: list[AskAction] = Field(default_factory=list)
    model_config = ConfigDict(protected_namespaces=())


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


class GenerationAttemptSummary(CamelModel):
    """Latest durable generation state for one USER message."""

    latest_attempt_id: str
    status: str
    retryable: bool
    failure_code: str | None = None


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
    component_data: dict | list[dict] | None = None
    # Nullable by design: old rows have no recorded scope and must make no claim.
    answer_scope: AskScope | None = None
    token_count: int
    model_name: str | None = None
    reply_to_message_id: str | None = None
    generation: GenerationAttemptSummary | None = None
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
