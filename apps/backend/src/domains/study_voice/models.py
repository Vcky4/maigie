"""Request and response shapes for the voice REST surface.

**Every field here is snake_case, and that is not an oversight.** The rest of the API is camelCase, but two
shipped clients — `geminiLiveApi.ts` on web and `StudyVoiceModal.tsx` on mobile — were written against these
exact names, and renaming them would break voice study on a released mobile build to make a JSON key prettier.
The inconsistency is a real debt and it is recorded in the design document's open decisions along with the
`gemini-live` path itself; both should be fixed in one coordinated rename, not silently here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StartConversationRequest(BaseModel):
    """What the client asks for when opening a session."""

    #: Ignored. Session ids are issued by the server; a client-chosen id would let one learner name
    #: another learner's session. Kept because both clients send the field.
    session_id: str | None = None
    #: **Ignored.** The tutor brief is composed server-side by `context.build_brief`. A system instruction
    #: from the browser is a tutor written by the browser — it could ask for the answer key or drop the
    #: framing entirely. Kept in the shape because both clients send it.
    system_instruction: str | None = None
    course_id: str | None = None
    topic_id: str | None = None


class StartConversationResponse(BaseModel):
    session_id: str
    status: str
    #: Empty string rather than null: the field is required in the clients' type and no chat session is
    #: created for a voice sitting. Left in place because removing it would be a breaking change for a
    #: value nothing reads.
    chat_session_id: str = ""
    study_session_id: str | None = None
    course_id: str | None = None
    topic_id: str | None = None


class ConversationSummary(BaseModel):
    session_id: str
    status: str


class ConversationListResponse(BaseModel):
    sessions: list[ConversationSummary] = Field(default_factory=list)


class ConversationStatusResponse(BaseModel):
    session_id: str
    status: str
    user_id: str


class StopConversationResponse(BaseModel):
    session_id: str
    status: str


class StudyDiagramRequest(BaseModel):
    topic_id: str
    topic_title: str | None = None
    course_title: str | None = None
    #: What the learner wants illustrated. Absent means "whatever they are stuck on right now".
    hint: str | None = None
    #: The tail of the spoken transcript, sent by the client so the diagram follows the conversation.
    transcript_tail: str | None = None


class StudyDiagramResponse(BaseModel):
    #: Mermaid body with no fences. Empty when an equation alone answers the question.
    mermaid: str
    #: LaTeX for one display equation, no delimiters. Empty when a diagram alone answers it.
    display_math: str
    caption: str
    #: Id of the stored `TopicIllustration`, so the client can delete the one it just asked for.
    #:
    #: Null when the diagram was generated but could not be kept. The diagram is still returned and still
    #: charged for, because the learner has it either way — see `illustration_service`. A null here means
    #: "this one will not be on the lesson page later", not "this one failed".
    illustration_id: str | None = None
