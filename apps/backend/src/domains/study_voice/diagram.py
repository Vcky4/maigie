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
        # 8192, from 1200 and then 2048, both of which truncated in practice.
        #
        # The output itself is tiny — a real diagram is around 700 characters — so this looks absurdly
        # generous until you account for where the budget actually goes: the configured model is a *thinking*
        # model, and reasoning tokens are drawn from the same output allowance. A model that spends 1,900
        # tokens deciding what to draw and then starts writing has 148 left, which produces exactly the
        # symptom seen here — a reply cut off mid-label. The lesson route reached 8192 by the same route.
        #
        # Costing nothing when unused: billing is on tokens actually produced, not on the ceiling.
        max_tokens=8192,
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
        operation="study_diagram",
    )
    if not isinstance(result, dict):
        raise ValidationError("The model did not return a diagram.")

    mermaid = str(result.get("mermaid") or "").strip()
    display_math = str(result.get("display_math") or "").strip()
    if not mermaid and not display_math:
        raise ValidationError("The model returned an empty diagram.")

    # A diagram cut off mid-draw is worse than none, and it reaches here looking valid.
    #
    # `generate_content_json` repairs truncated replies by closing the dangling JSON string and brackets.
    # For a lesson that is right: the sections are a list, so the complete ones survive and the parsers
    # discard the incomplete last one. A mermaid diagram is not a list — it is **one indivisible value** — so
    # the same repair converts "the reply was cut off" into "here is valid JSON containing half a diagram",
    # which parses, stores, costs 80 credits, and then fails to render in the browser.
    #
    # Verified rather than assumed: the diagram from the reported failure was parsed against mermaid itself,
    # every construct in it is legal, and the only reason it would not draw is that it stops mid-label.
    if mermaid and _looks_truncated(mermaid):
        raise ValidationError("The diagram was cut off before it finished. Try asking again.")

    return {
        "mermaid": mermaid,
        "display_math": display_math,
        "caption": str(result.get("caption") or "").strip(),
    }


def _looks_truncated(mermaid: str) -> bool:
    """Whether a mermaid body stops in the middle of something.

    Three cheap structural checks rather than a mermaid parser, which does not exist in Python. Each one is a
    thing a *complete* diagram never does, so a false positive costs one regeneration and a false negative is
    caught by the renderer's own failure panel — the asymmetry that makes a heuristic acceptable here.

    Deliberately not checking for balanced parentheses: they appear inside labels far more often than they
    delimit anything, and `Churn: Yes (Duplicate)` is ordinary content.
    """
    # An odd number of double quotes means a label was opened and never closed — the exact shape of the
    # reported failure, which ended `C1["ID 101 | Plan: Basic`.
    if mermaid.count('"') % 2 != 0:
        return True
    # Every `subgraph` needs its `end`. More subgraphs than `end`s means the diagram stops inside one.
    if mermaid.count("subgraph ") > len(
        [line for line in mermaid.splitlines() if line.strip() == "end"]
    ):
        return True
    # Unbalanced node brackets. Counted across the whole body rather than per line, because a label may
    # legitimately contain a bracket only when quoted — and an unquoted stray one is its own defect.
    if mermaid.count("[") != mermaid.count("]"):
        return True
    return False
