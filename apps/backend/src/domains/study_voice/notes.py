"""Turning a voice session into a note — when, and only when, the learner asks.

## Why this is a request and not a side effect

The deleted implementation did this automatically: every six transcript fragments it summarised the recent
conversation and appended the result to a `Note` on the topic, and at the end of the session it wrote another
one. The learner was never asked and could not decline. Two things are wrong with that beyond the consent
question:

- **A conversation is where you are allowed to be wrong.** Thinking aloud badly is how spoken tutoring works.
  Writing that into the learner's own notes turns their notes into a record of their mistakes.
- **It made the transcript worth keeping.** Any automatic writer needs the conversation on hand, which is how
  transcripts end up in a table, which is how a retention policy becomes something you owe someone.

So this is called from one place: a `save_note` frame the learner's own button sends. Nothing else calls it.
If they never press it, the conversation is never written anywhere and is gone when the socket closes.

## One note per session

Pressing it twice does not make two notes. The note id is remembered on the session record, and a second
request rewrites that note from the fuller conversation. `note_service.update_note` snapshots the previous
version first, so a learner who edited the note by hand between saves can get their wording back rather than
discovering it silently replaced.
"""

from __future__ import annotations

import logging

from src.domains.billing.services.credit_consumption_service import (
    CREDIT_COSTS,
    check_credit_availability,
    consume_credits,
)
from src.domains.identity.db_models import User
from src.domains.personal_learning.services import note_service
from src.domains.personal_learning.services.llm_resilient import generate_content
from src.shared.exceptions import NotFoundError, SubscriptionLimitError, ValidationError

from . import session_store
from .session_store import VoiceSession
from .transcript import MIN_TURNS_FOR_NOTE, SessionTranscript

logger = logging.getLogger(__name__)

_MAX_TRANSCRIPT_CHARS = 14000
_DEFAULT_TITLE = "Voice study session"

_PROMPT = (
    "Below is the transcript of a spoken tutoring session between a learner and their tutor.\n"
    "Write the note the learner would have written for themselves afterwards.\n"
    "\n"
    "Output exactly two parts and nothing else:\n"
    "TITLE: a short, specific title naming what was actually discussed\n"
    "CONTENT: the note body in Markdown\n"
    "\n"
    "Rules for the body:\n"
    "- Write what was worked out, in the learner's interest, not a description of the conversation. "
    'Never write "the tutor explained" or "we discussed".\n'
    "- Where the learner was confused and then understood something, record the understanding and what the "
    "confusion was, because that is the part worth revisiting.\n"
    "- Keep anything the learner got wrong factual and unembarrassing: state the correct idea and note it "
    "as something to check again.\n"
    "- Do not invent anything that is not in the transcript. If the conversation was thin, write a short "
    "note. A short honest note is better than a padded one.\n"
    "- No preamble, no closing summary, no headings above the first point.\n"
    "\n"
    "Transcript:\n"
)


async def save_session_note(
    user: User,
    session: VoiceSession,
    transcript: SessionTranscript,
) -> dict[str, object]:
    """Write or rewrite this session's note. Returns `{note_id, title, content, created}`.

    Raises `ValidationError` when there is not enough conversation, and `SubscriptionLimitError` when the
    learner cannot pay for the generation. Both are reported to the learner rather than swallowed — they
    pressed a button and are owed an answer either way.
    """
    if not transcript.has_enough_for_a_note():
        raise ValidationError(
            "There is not enough of a conversation to write a note yet. "
            f"Keep talking — this needs at least {MIN_TURNS_FOR_NOTE} exchanges."
        )
    if not session.topic_id:
        raise ValidationError("A note needs a lesson to belong to, and this session has none.")

    cost = CREDIT_COSTS.get("voice_session_note", 100)
    available, message = await check_credit_availability(user, cost)
    if not available:
        raise SubscriptionLimitError(
            message="Not enough credits to write this note.",
            detail=message or "Not enough credits to write this note.",
        )

    title, content = await _write(user.id, transcript.render(_MAX_TRANSCRIPT_CHARS))

    note = None
    created = False
    if session.note_id:
        try:
            note = await note_service.update_note(
                user_id=user.id, note_id=session.note_id, data={"title": title, "content": content}
            )
        except NotFoundError:
            # The learner deleted the note between saves. Tidying up is not an error, so write a new one
            # rather than reporting a failure.
            logger.info("Session note %s is gone — writing a new one", session.note_id)

    if note is None:
        note = await note_service.create_note(
            user_id=user.id,
            data={
                "title": title,
                "content": content,
                "topicId": session.topic_id,
                "courseId": session.course_id,
            },
        )
        created = True
        await session_store.remember_note(session.session_id, note.id)

    # Charged after the note exists. A failed generation is not something to bill for.
    try:
        await consume_credits(user, cost, operation="voice_session_note")
    except SubscriptionLimitError:
        # The note is already written and the learner can see it. Refusing to hand it over now would be
        # worse than absorbing the cost of one generation.
        logger.warning(
            "Voice session note for user %s was written but could not be billed", user.id
        )

    return {
        "note_id": note.id,
        "title": note.title,
        "content": note.content or "",
        "created": created,
    }


async def _write(user_id: str, transcript_text: str) -> tuple[str, str]:
    """Ask for a title and a body, and take what comes back literally.

    The two-line format is parsed rather than guessed at: a reply that does not follow it falls back to
    using the whole response as the body, which is worse-looking but still the learner's session rather than
    an error message where their note should be.
    """
    response = await generate_content(
        _PROMPT + transcript_text,
        max_tokens=2048,
        temperature=0.4,
        user_id=user_id,
    )
    text = (response or "").strip()
    if not text:
        raise ValidationError("The note could not be written. Try again in a moment.")

    title = ""
    body_lines: list[str] = []
    in_content = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not in_content and stripped.upper().startswith("TITLE:"):
            title = stripped[6:].strip()
            continue
        if stripped.upper().startswith("CONTENT:"):
            in_content = True
            remainder = stripped[8:].strip()
            if remainder:
                body_lines.append(remainder)
            continue
        if in_content:
            body_lines.append(line)

    content = "\n".join(body_lines).strip()
    if not content:
        # The model ignored the format. Its answer is still about this session, so keep it.
        content = text
    return (title or _DEFAULT_TITLE)[:500], content[:50000]
