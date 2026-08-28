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
request revises that note. `note_service.update_note` snapshots the previous version first, so a learner who
edited the note by hand between saves can get their wording back rather than discovering it silently
replaced.

## Revising, not replacing

A second pass is given **the note that exists plus the conversation**, and asked to extend it. The first
implementation sent only the transcript and overwrote whatever was there, which loses two different things:

- **The learner's own edits.** They fix a mistake or add a thought, the next write regenerates from the
  transcript, and their wording is gone from `content` — recoverable only by digging through version history.
- **The early part of a long session.** The transcript buffer is bounded (`MAX_TURNS`, `MAX_CHARS`) and
  `render` cuts it again, so in a long sitting the opening conversation has aged out of memory. Overwriting
  then replaces a note that *did* cover the beginning with one that cannot, and the source no longer exists
  to recover it from.

The second is the one that makes this necessary rather than nice. Once the note is the durable record, the
transcript can stay a disposable buffer — which is the property this module was designed around, and
revision strengthens it rather than trading against it.

The prompt for a revision is deliberately conservative: keep what is there, add what is new, correct only
what the conversation actually contradicts. Rewriting freely on every pass is a telephone game, and a note
revised five times would drift away from what was said the first time.

## When the note is written

Two moments, and the toggle decides whether the second happens:

- **On request**, as before — the learner asks and gets a note immediately.
- **At the end of the session**, if note-taking was switched on. That is the normal path and the reason the
  toggle exists: one generation covering the whole sitting, rather than a charge every few minutes. It runs
  from the relay's teardown, on the same `on_done` hook credit settlement uses, so it survives a socket
  drop, a cancelled task and a closed tab.
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

#: The revision prompt, used when a note for this session already exists.
#:
#: Deliberately narrow. The obvious instruction — "here is a note and a transcript, write a better note" —
#: lets the model rewrite everything on every pass, which over several revisions drifts away from what was
#: actually said and quietly discards anything the learner typed themselves. So the existing note is framed
#: as the thing to *keep*, and the transcript as the thing to fold in.
_REVISE_PROMPT = (
    "Below is a note a learner already has, followed by the full transcript of the spoken tutoring "
    "session it came from. The session has continued since the note was written.\n"
    "Produce the updated note.\n"
    "\n"
    "Output exactly two parts and nothing else:\n"
    "TITLE: a short, specific title naming what was actually discussed\n"
    "CONTENT: the note body in Markdown\n"
    "\n"
    "Rules:\n"
    "- **Keep the existing note.** Preserve its points, its wording and its order. Parts of it may have "
    "been written or edited by the learner themselves, and parts may describe conversation that is no "
    "longer in the transcript below — both must survive.\n"
    "- Add what the rest of the conversation covered, in the same voice.\n"
    "- Change an existing point only where the conversation actually corrected it. Then state the correct "
    "idea; do not narrate the correction.\n"
    "- Merge a repeated point rather than listing it twice.\n"
    "- Only widen the title if the session has genuinely moved beyond what it names.\n"
    '- Write what was worked out, in the learner\'s interest. Never write "the tutor explained" or '
    '"we discussed".\n'
    "- Do not invent anything that is in neither the note nor the transcript.\n"
    "- No preamble, no closing summary, no headings above the first point.\n"
    "\n"
    "Existing note:\n"
)


async def save_session_note(
    user: User,
    session: VoiceSession,
    transcript: SessionTranscript,
) -> dict[str, object]:
    """Write or revise this session's note. Returns `{note_id, title, content, created}`.

    Raises `ValidationError` when there is not enough conversation — or not enough *new* conversation since
    the last note — and `SubscriptionLimitError` when the learner cannot pay for the generation. Both are
    reported rather than swallowed on the request path: the learner pressed a button and is owed an answer.
    """
    if not transcript.has_enough_for_a_note():
        raise ValidationError(
            "There is not enough of a conversation to write a note yet. "
            f"Keep talking — this needs at least {MIN_TURNS_FOR_NOTE} exchanges."
        )
    if not session.topic_id:
        raise ValidationError("A note needs a lesson to belong to, and this session has none.")

    # Nothing said since the last note means there is nothing to add to it. Refused rather than run, because
    # a second pass over identical material spends a model call and 100 credits to produce the note that is
    # already on screen. This matters most on the teardown path, which fires whether or not the learner kept
    # talking after writing one by hand.
    existing_note = None
    if session.note_id:
        new_turns = transcript.turn_count - session.turns_at_last_note
        if new_turns < MIN_TURNS_FOR_NOTE:
            raise ValidationError("Your note is already up to date with this conversation.")
        # Fetched before the generation, because a revision has to be given what it is revising. A note the
        # learner deleted in the meantime reads as "write a fresh one" rather than as a failure.
        try:
            existing_note = await note_service.get_note(user_id=user.id, note_id=session.note_id)
        except NotFoundError:
            logger.info("Session note %s is gone — writing a new one", session.note_id)

    # Checked before the generation and charged after it, so a learner who cannot pay is told before a model
    # call is spent, and a generation that fails is not billed. Both halves matter: the check alone would
    # bill for failures, and the charge alone would spend a call the learner cannot cover.
    cost = CREDIT_COSTS.get("voice_session_note", 100)
    available, message = await check_credit_availability(user, cost)
    if not available:
        raise SubscriptionLimitError(
            message="Not enough credits to write this note.",
            detail=message or "Not enough credits to write this note.",
        )

    rendered = transcript.render(_MAX_TRANSCRIPT_CHARS)
    if existing_note is not None:
        title, content = await _revise(
            user.id,
            existing_title=existing_note.title or _DEFAULT_TITLE,
            existing_content=existing_note.content or "",
            transcript_text=rendered,
        )
    else:
        title, content = await _write(user.id, rendered)

    note = None
    created = False
    if existing_note is not None:
        try:
            note = await note_service.update_note(
                user_id=user.id, note_id=existing_note.id, data={"title": title, "content": content}
            )
        except NotFoundError:
            # Deleted between the read and the write. Rare, and the answer is the same as above.
            logger.info("Session note %s vanished mid-write — writing a new one", existing_note.id)

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

    # Recorded on both paths, not just creation. The marker is what the "nothing new to say" check above
    # reads, so a revision that did not update it would let the next pass re-run over the same turns.
    await session_store.remember_note(session.session_id, note.id, turns=transcript.turn_count)

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


async def finalise_session_note(
    user: User,
    session: VoiceSession,
    transcript: SessionTranscript,
) -> dict[str, object] | None:
    """Write the note at the end of a session, if the learner asked for one. Never raises.

    This is the normal path, and the toggle is what authorises it: with `note_taking` off this returns
    immediately and the conversation is never written anywhere. One generation covering the whole sitting is
    the point — the alternative considered was a periodic refresh, which at 100 credits a pass would charge a
    forty-minute session eight times for one note.

    ## Why it cannot raise

    It runs from the relay's teardown, on the same hook that settles the bill. An exception escaping here
    would ride up through a `finally` whose other job is charging for the session, so a model outage could
    become a free session or a lost settlement. There is also nobody left to tell: the socket is closing.
    Failures are logged and the conversation is lost, which is the honest outcome — the learner asked for a
    note and did not get one, and inventing a partial one would be worse.

    ## Why the session is passed in rather than looked up

    **This took the caller's word for it and it was wrong to.** The first version re-read the record by id,
    reasoning that `note_taking` is toggled mid-session so the copy taken at `start_session` is stale. The
    reasoning was right and the mechanism was not: the web client's exit path closes the socket and
    *immediately* fires `POST /conversation/{id}/stop`, which **deletes the record**. Teardown then read
    `None` and returned silently, so a learner who switched note-taking on got no note and no error.

    The socket now holds its own copy and keeps it in step with every toggle and every manual save, and passes
    it here. The stored record is still the source of truth across reconnects, which is what it is for — a
    fresh socket reads it in `start`. It is simply not something a teardown can rely on still existing.
    """
    try:
        if not session.note_taking:
            return None
        if session.user_id != user.id:
            # Cannot happen through the socket, which checks ownership before starting. Guarded anyway,
            # because this writes a note into somebody's library and the check is one comparison.
            logger.warning("Refusing to finalise a note for a session belonging to another learner")
            return None

        saved = await save_session_note(user, session, transcript)
    except ValidationError as exc:
        # The ordinary endings: a session too short to write about, or one already up to date because the
        # learner wrote the note by hand and then stopped talking. Neither is a fault.
        logger.info("No end-of-session note for user %s: %s", user.id, exc)
        return None
    except SubscriptionLimitError:
        logger.info("End-of-session note for user %s was not written: out of credits", user.id)
        return None
    except Exception:
        logger.exception("Failed to write the end-of-session note for user %s", user.id)
        return None

    logger.info("Wrote end-of-session note %s for user %s", saved.get("note_id"), user.id)
    return saved


async def _write(user_id: str, transcript_text: str) -> tuple[str, str]:
    """A first note, from the conversation alone."""
    return await _generate(user_id, _PROMPT + transcript_text)


async def _revise(
    user_id: str,
    *,
    existing_title: str,
    existing_content: str,
    transcript_text: str,
) -> tuple[str, str]:
    """An updated note, from the note that exists plus the conversation.

    A larger budget than a first write: the reply has to carry the whole existing note forward as well as
    what is new, and a truncated revision is a note with its ending cut off — which, because this replaces
    the note's content, would *lose* the part that was cut. That is the one failure mode here worse than not
    revising at all.

    The existing note is passed whole rather than trimmed. Trimming it would silently drop the oldest points,
    which are precisely the ones the transcript can no longer supply.
    """
    prompt = (
        f"{_REVISE_PROMPT}TITLE: {existing_title}\nCONTENT:\n{existing_content}\n\nTranscript:\n"
        f"{transcript_text}"
    )
    return await _generate(user_id, prompt, max_tokens=4096)


async def _generate(user_id: str, prompt: str, *, max_tokens: int = 2048) -> tuple[str, str]:
    """Ask for a title and a body, and take what comes back literally.

    The two-line format is parsed rather than guessed at: a reply that does not follow it falls back to
    using the whole response as the body, which is worse-looking but still the learner's session rather than
    an error message where their note should be.
    """
    response = await generate_content(
        prompt,
        max_tokens=max_tokens,
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
