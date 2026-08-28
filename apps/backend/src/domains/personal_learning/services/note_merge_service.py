"""Combining several notes into one.

## Why this exists

A voice session writes one note per sitting, and `Note.topicId` is a plain foreign key, so studying one
lesson across five short sittings leaves five thin notes on it. That is worse for revision than one
consolidated note, and it is not something the learner can fix by hand without retyping.

## Why the learner chooses which, rather than the product detecting them

The first design was "offer to merge the short session notes on this topic". Two problems killed it. There is
**no provenance on `Note`** — nothing records that a note came from a voice session — so it could not tell a
session note from one the learner typed by hand or generated from a document on the same topic, and it would
have swept those up. And "short" is a guess: a threshold either nags about notes that were deliberately brief
or misses the case it was written for.

So the learner selects. It needs no column, cannot pick up something they did not intend, works for
hand-written notes as a bonus rather than a compromise, and the person who knows which notes belong together
is the one who wrote them.

## Why the originals are archived and not deleted

A merge is one model call reading several inputs, so it can drop something. "Your five notes are now one note
and the five are gone" is not a claim worth making without a way back. `Note.archived` already exists, is one
flag, and is fully reversible — so the originals leave the library without leaving the account.

Deleting them would also destroy their attachments and their version history, which the merged note has no
way to carry: attachments are rows on the note, and a merge is not an edit of any one of them.

## The prompt preserves rather than summarises

Several inputs may contain the learner's own writing — hand-edited session notes, or notes they wrote
themselves. A merge that "tidies" them is the same loss the old note-overwrite had, at five notes instead of
one. So the instruction is to keep every distinct point and remove only genuine duplication.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domains.billing.services.credit_consumption_service import (
    CREDIT_COSTS,
    check_credit_availability,
    consume_credits,
)
from src.domains.identity.db_models import User
from src.shared.exceptions import NotFoundError, SubscriptionLimitError, ValidationError

from ..repository import personal_learning_repo as repo
from . import note_service
from .llm_resilient import generate_content

logger = logging.getLogger(__name__)

#: Fewer than this and there is nothing to combine.
MIN_NOTES = 2

#: More than this in one merge and the reply cannot hold them. A learner with thirty notes on one topic can
#: merge in batches, which is also easier to check than one enormous rewrite.
MAX_NOTES = 10

_MAX_INPUT_CHARS = 24000

_PROMPT = (
    "Below are several notes the same learner wrote about one lesson, in the order they were written.\n"
    "Combine them into a single note.\n"
    "\n"
    "Output exactly two parts and nothing else:\n"
    "TITLE: a short, specific title covering what these notes are about\n"
    "CONTENT: the combined note body in Markdown\n"
    "\n"
    "Rules:\n"
    "- **Keep every distinct point.** This replaces the learner's notes, so anything dropped here is lost "
    "to them. Some of this is their own writing.\n"
    "- Remove only genuine duplication — the same point made twice. Where two notes say the same thing "
    "with different detail, keep the fuller version.\n"
    "- Group related points together so the result reads as one note rather than several stitched "
    "end to end. Do not label the sections by which note they came from.\n"
    "- Keep the learner's own wording where you can. Do not rewrite for style.\n"
    "- Where two notes genuinely disagree, keep both and say which is later.\n"
    "- Do not add anything that is not in the notes. Do not summarise or shorten for its own sake.\n"
    "- No preamble, no closing summary, no headings above the first point.\n"
    "\n"
    "Notes:\n"
)


async def merge_notes(user: User, *, note_ids: list[str]) -> Any:
    """Combine the named notes into a new one, archiving the originals.

    Every id is resolved through `note_service.get_note`, which is scoped to the owner — so one id belonging
    to somebody else fails the whole request rather than being skipped. Filtering silently is the
    accept-and-discard pattern this codebase has a guard against: the learner asked for five notes to be
    combined and would be shown a note made from four.

    Raises `ValidationError` for a list that is too short, too long, contains duplicates, or spans notes with
    nothing to write from; `NotFoundError` when an id is not the learner's; `SubscriptionLimitError` when the
    generation cannot be paid for.
    """
    unique = list(dict.fromkeys(note_ids))
    if len(unique) != len(note_ids):
        # Refused rather than de-duplicated. A repeated id means the caller built its list wrongly, and
        # quietly merging four notes when five were named is the kind of "success" this codebase treats as a
        # defect.
        raise ValidationError("The same note was listed more than once.")
    if len(unique) < MIN_NOTES:
        raise ValidationError(f"Combining notes needs at least {MIN_NOTES} of them.")
    if len(unique) > MAX_NOTES:
        raise ValidationError(
            f"Up to {MAX_NOTES} notes can be combined at once. Combine them in batches."
        )

    notes = [await note_service.get_note(user_id=user.id, note_id=note_id) for note_id in unique]

    if not any((note.content or "").strip() for note in notes):
        raise ValidationError("These notes have no content to combine.")

    # Ordered oldest first, so the combined note reads as the learner's understanding developing forwards and
    # "which is later" in the prompt above means something. `get_note` returns them in the order asked for,
    # which is the client's display order — newest first — and is the wrong way round for this.
    notes.sort(key=lambda note: note.created_at)

    cost = CREDIT_COSTS.get("note_merge", 100)
    available, message = await check_credit_availability(user, cost)
    if not available:
        raise SubscriptionLimitError(
            message="Not enough credits to combine these notes.",
            detail=message or "Not enough credits to combine these notes.",
        )

    title, content = await _combine(user.id, notes)

    # The lesson the merged note belongs to. Taken from the inputs rather than from a parameter, and only
    # when they agree: combining notes from two topics is allowed, and the result then belongs to neither
    # rather than being filed under whichever happened to be first.
    topic_ids = {note.topic_id for note in notes if note.topic_id}
    course_ids = {note.course_id for note in notes if note.course_id}

    merged = await note_service.create_note(
        user_id=user.id,
        data={
            "title": title,
            "content": content,
            "topicId": next(iter(topic_ids)) if len(topic_ids) == 1 else None,
            "courseId": next(iter(course_ids)) if len(course_ids) == 1 else None,
            # Every tag from every input, so the merged note is findable by anything its parts were.
            "tags": sorted({tag.tag for note in notes for tag in (note.tags or [])}),
        },
    )

    # Archived only after the merged note exists. The other order risks a learner with their notes hidden and
    # nothing to show for it.
    for note in notes:
        try:
            await repo.update_note(note.id, {"archived": True})
        except Exception:
            # The merged note is written and is the thing the learner asked for. A note that failed to
            # archive is visible clutter, not lost work, so this is reported and not raised.
            logger.warning("Could not archive note %s after merging it", note.id)

    try:
        await consume_credits(user, cost, operation="note_merge")
    except SubscriptionLimitError:
        # Same reasoning as the session note: the work is done and the learner can see it. Refusing to hand
        # it over would be worse than absorbing one generation.
        logger.warning("Note merge for user %s was completed but could not be billed", user.id)

    logger.info("Merged %d notes into %s for user %s", len(notes), merged.id, user.id)
    return merged


async def _combine(user_id: str, notes: list[Any]) -> tuple[str, str]:
    """Ask for one note covering all of them."""
    blocks: list[str] = []
    for index, note in enumerate(notes, start=1):
        body = (note.content or "").strip() or "(no content)"
        blocks.append(f"--- Note {index}: {note.title} ---\n{body}")
    joined = "\n\n".join(blocks)
    if len(joined) > _MAX_INPUT_CHARS:
        # Trimmed from the *end*, unlike a transcript, which is trimmed from the start. A transcript's recent
        # turns matter most; here the notes are ordered oldest first and every one is equally the learner's,
        # so cutting the tail at least keeps whole early notes rather than half of each.
        joined = joined[:_MAX_INPUT_CHARS]

    response = await generate_content(
        _PROMPT + joined,
        max_tokens=4096,
        temperature=0.3,
        user_id=user_id,
    )
    text = (response or "").strip()
    if not text:
        raise ValidationError("These notes could not be combined. Try again in a moment.")

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
        # The model ignored the format. Its answer is still about these notes, and this replaces them, so
        # keeping it is better than discarding the one thing that read all of them.
        content = text

    fallback_title = notes[0].title if notes else "Combined notes"
    return (title or fallback_title)[:500], content[:50000]


__all__ = ["MAX_NOTES", "MIN_NOTES", "merge_notes"]
