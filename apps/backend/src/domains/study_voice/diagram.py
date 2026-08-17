"""One-off diagram or equation for the study screen.

A native-audio model speaks; it cannot draw. `study_show_visual` exists so the model can push a diagram to
the screen mid-conversation, but it only fires when the model decides to call it. This is the learner asking
directly — "show me this" — answered by a text model rather than the live one.

Ported from `generate_study_diagram_for_topic` at `4953972^`. Two changes:

- **Routed through `llm_resilient`** instead of constructing a `google.genai` client here, so the diagram
  follows the learner's provider preference, gets the retry and circuit breaker every other generation gets,
  and does not hard-code a model name in a second place.
- **Ownership checked properly.** The original compared `course.userId` inline and raised `ValueError`, which
  the route turned into a `400`. Answering "bad request" to "this topic is not yours" is the wrong answer to
  the wrong question; `check_topic_ownership` gives `404` and `403` like every other topic read.
"""

from __future__ import annotations

import logging

from src.domains.knowledge.services.course_service import check_topic_ownership
from src.domains.personal_learning.repository import personal_learning_repo
from src.domains.personal_learning.services.llm_resilient import generate_content_json
from src.shared.exceptions import ValidationError

logger = logging.getLogger(__name__)

_MAX_NOTE_CHARS = 8000
_MAX_TRANSCRIPT_CHARS = 6000


async def generate_for_topic(
    user_id: str,
    *,
    topic_id: str,
    topic_title: str | None = None,
    course_title: str | None = None,
    hint: str | None = None,
    transcript_tail: str | None = None,
) -> dict[str, str]:
    """Return `{mermaid, display_math, caption}` for what the learner is trying to picture.

    At least one of `mermaid` and `display_math` comes back non-empty, or this raises. An empty diagram
    rendered as a blank overlay would look like a broken feature rather than a refusal.
    """
    topic, _module, course = await check_topic_ownership(topic_id, user_id)

    notes, _total = await personal_learning_repo.list_notes(
        user_id, where={"topicId": topic_id, "archived": False}, take=20
    )
    bodies = [(n.content or "").strip() for n in reversed(notes) if (n.content or "").strip()]
    note_blob = "\n\n---\n\n".join(bodies) if bodies else "(no notes yet)"
    if len(note_blob) > _MAX_NOTE_CHARS:
        note_blob = note_blob[-_MAX_NOTE_CHARS:]

    tail = (transcript_tail or "").strip()
    if len(tail) > _MAX_TRANSCRIPT_CHARS:
        tail = tail[-_MAX_TRANSCRIPT_CHARS:]

    subject = (hint or "").strip() or "The main idea the learner is trying to understand right now."

    prompt = (
        "You help visualize ideas during a live voice study session.\n"
        "Return ONLY a single JSON object with these keys:\n"
        '- "mermaid": a valid Mermaid diagram body WITHOUT backtick fences, at most 30 lines. Start with '
        "flowchart TD, graph LR, sequenceDiagram or mindmap. Any node label containing parentheses, "
        'brackets, colons or slashes MUST be double-quoted, e.g. A["V (vectors)"] and never '
        'A[V (vectors)]. Use "" if a diagram is not appropriate.\n'
        '- "display_math": LaTeX for ONE display equation with no $ or $$ delimiters, or "".\n'
        '- "caption": one short line, or "".\n'
        "At least one of mermaid or display_math must be non-empty.\n\n"
        f"Course: {course_title or course.title}\n"
        f"Topic: {topic_title or topic.title}\n\n"
        f"The learner's notes:\n{note_blob}\n\n"
        f"Recent voice transcript, which may be fragmented:\n{tail or '(none)'}\n\n"
        f"What to illustrate: {subject}\n"
    )

    result = await generate_content_json(
        prompt,
        # Raised from 1200. The reply has to carry up to thirty lines of mermaid, a LaTeX equation and a
        # caption, JSON-escaped — and this is the third time in this programme a generation has been sized
        # for the happy case and truncated on a real one. 1200 was not the cause of the `500` below (an empty
        # reply is not a truncated one) but it is close enough to the ceiling to be worth moving.
        max_tokens=2048,
        temperature=0.4,
        # **The fix for a `500` on this route.** `fallback=None` — the default, and what this call site used
        # to pass by omission — means "no fallback, raise", not "return None". So an unparseable or empty
        # model reply escaped as a raw `JSONDecodeError` all the way out of the request, and the learner got
        # an unhandled `500` with a stack trace instead of "try again".
        #
        # This is the *same defect* that was found and fixed on the lesson-generation and outline routes; the
        # ambiguity is documented on `generate_content_json` itself and this third call site was missed. `{}`
        # is a dict, so it passes the shape check below and falls into the empty-diagram branch, which is the
        # actionable error the route turns into a `502`.
        fallback={},
        user_id=user_id,
    )
    if not isinstance(result, dict):
        raise ValidationError("The model did not return a diagram.")

    mermaid = str(result.get("mermaid") or "").strip()
    display_math = str(result.get("display_math") or "").strip()
    if not mermaid and not display_math:
        raise ValidationError("The model returned an empty diagram.")

    return {
        "mermaid": mermaid,
        "display_math": display_math,
        "caption": str(result.get("caption") or "").strip(),
    }
