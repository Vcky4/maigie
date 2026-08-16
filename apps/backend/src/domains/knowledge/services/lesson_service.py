"""Generating a lesson, and keeping it.

`POST .../topics/{id}/generate` used to compose a prompt, receive a lesson, return it and store
nothing. The learner read it once; opening the topic again generated a different one, or showed an
empty page, and every credit spent produced an artifact that existed only in one HTTP response. That
is what this module fixes.

## Why the model is asked for structure rather than prose

The lesson surface renders a sequence of typed sections, each with its own title, key idea, steps and
code, and each separately completable. A markdown blob cannot answer that: to know where section
three begins you would parse headings, and to record that the learner finished it you would need an
identity that a heading does not have. So the model returns JSON, the sections become rows, and the
markdown is kept alongside as `Topic.content` for the readers that want a whole document — Study
Mode's voice tutor, and the reader's own fallback when a topic has no sections.

Both are stored, which is duplication, and it is deliberate: the markdown is the source the model
produced and the rows are the structure the page needs. Deriving the markdown from the rows on every
read would mean re-rendering prose the model already wrote; deriving the rows from the markdown is
the parse this design exists to avoid.

## What each generation type persists

Only `explain` writes the lesson, because only `explain` *is* the lesson. The other three are study
aids derived from a topic rather than versions of it:

- `explain` — replaces the body: `content`, `objectives`, `knowledgeCheck`, and the sections.
- `flashcards` — creates real `Flashcard` rows through `flashcard_service`, which is where cards live
  and where SM-2 scheduling picks them up. It used to return markdown that no deck ever saw.
- `quiz` and `summary` — returned, not stored. A summary written into `Topic.content` would overwrite
  the lesson with a condensation of it, and a five-question quiz is not the one-question check the
  topic holds. Real quizzes belong to the preparation domain, which already models attempts.

The response says which of these happened rather than leaving the caller to infer it from the type.
"""

from __future__ import annotations

import logging
from typing import Any

from ..repository import knowledge_repo

logger = logging.getLogger(__name__)

#: How many sections a generated lesson may contain. A ceiling rather than a target: the model
#: occasionally returns a section per sentence, and a lesson of forty one-line steps is a worse
#: reading experience than one of six, as well as forty rows and forty completion writes.
_MAX_SECTIONS = 12
_MAX_OBJECTIVES = 6
_VALID_KINDS = {"concept", "example", "algorithm", "comparison", "check"}


def build_lesson_prompt(title: str, existing_content: str | None = None) -> str:
    """Ask for a lesson as JSON, in the shape the reader renders.

    The field names match the response models exactly, so a well-behaved reply needs no translation.
    The instruction to omit rather than invent matters: asked for `code` on a section about study
    habits, a model will cheerfully produce a code block, and an empty key is better than a
    fabricated one.
    """
    prompt = f"""Write a lesson teaching "{title}".

Return ONLY a JSON object, no prose around it, with this shape:

{{
  "objectives": ["what the learner will be able to do", "..."],
  "sections": [
    {{
      "title": "section title",
      "eyebrow": "a two or three word label, e.g. Core idea",
      "kind": "concept | example | algorithm | comparison | check",
      "summary": "one sentence saying what this section covers",
      "durationMinutes": 5,
      "paragraphs": ["the teaching prose", "..."],
      "keyIdea": "the single sentence worth remembering",
      "steps": [{{"title": "step name", "detail": "what happens"}}],
      "bullets": ["short points"],
      "code": "a code example if the subject is technical"
    }}
  ],
  "knowledgeCheck": {{
    "question": "one question testing the main idea",
    "explanation": "why the correct answer is correct",
    "choices": [
      {{"id": "a", "label": "an option", "correct": false}},
      {{"id": "b", "label": "the right option", "correct": true}}
    ]
  }}
}}

Rules:
- Between 3 and {_MAX_SECTIONS} sections, ordered as they should be read.
- At most {_MAX_OBJECTIVES} objectives.
- `paragraphs` is required on every section. `keyIdea`, `steps`, `bullets` and `code` are optional —
  omit a key entirely rather than inventing content to fill it. Do not include a code example unless
  the subject genuinely involves code.
- Exactly one choice in `knowledgeCheck` has "correct": true, and give between 3 and 4 choices."""

    if existing_content:
        prompt += f"\n\nBuild on this existing material rather than contradicting it:\n{existing_content[:2000]}"
    return prompt


def _clean_str(value: Any, limit: int = 4000) -> str | None:
    """A trimmed string, or None for anything that is not usable text.

    Models return `null`, `""`, and the literal string `"None"` for a field they had nothing for, and
    all three should read as absent rather than as content.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text[:limit]


def _clean_str_list(value: Any, limit: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = [cleaned for item in value if (cleaned := _clean_str(item))]
    return items[:limit] or None


def parse_lesson(payload: Any) -> dict[str, Any]:
    """Turn a model reply into section rows, objectives and a check.

    Everything is validated here rather than trusted, because the reply is the one input to this
    feature that no type system guards: the model is asked for a shape and returns something close
    to it. A malformed reply must degrade to fewer sections, never to a row that breaks the reader —
    a section with no title, or `steps` holding bare strings where the reader expects an object, would
    render as an empty step list on a page the learner is trying to read.

    Returns `sections: []` rather than raising when nothing survives. The caller then keeps the
    markdown it also received, and the reader falls back to it, so a bad generation costs structure
    rather than the whole lesson.
    """
    if not isinstance(payload, dict):
        return {"objectives": None, "sections": [], "knowledgeCheck": None}

    sections: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get("sections") or []):
        if not isinstance(raw, dict):
            continue
        title = _clean_str(raw.get("title"), 255)
        paragraphs = _clean_str_list(raw.get("paragraphs"), 20)
        # A section with no title has nothing for the outline to list, and one with no paragraphs has
        # nothing to read. Either way it would render as a clickable blank, so it is dropped.
        if not title or not paragraphs:
            continue

        kind = raw.get("kind")
        duration = raw.get("durationMinutes")
        steps = [
            {"title": step_title, "detail": detail}
            for step in (raw.get("steps") or [])
            if isinstance(step, dict)
            and (step_title := _clean_str(step.get("title"), 255))
            and (detail := _clean_str(step.get("detail")))
        ]

        sections.append(
            {
                # Spaced by ten, so a section can later be inserted between two without renumbering
                # the rest — the same reason `order` is a float.
                "order": float((index + 1) * 10),
                "kind": kind if kind in _VALID_KINDS else "concept",
                "title": title,
                "eyebrow": _clean_str(raw.get("eyebrow"), 120),
                "summary": _clean_str(raw.get("summary")),
                "durationMinutes": (
                    int(duration)
                    if isinstance(duration, int | float) and 0 < duration <= 600
                    else None
                ),
                "paragraphs": paragraphs,
                "keyIdea": _clean_str(raw.get("keyIdea")),
                "steps": steps or None,
                "bullets": _clean_str_list(raw.get("bullets"), 12),
                "code": _clean_str(raw.get("code"), 8000),
            }
        )
        if len(sections) >= _MAX_SECTIONS:
            break

    return {
        "objectives": _clean_str_list(payload.get("objectives"), _MAX_OBJECTIVES),
        "sections": sections,
        "knowledgeCheck": _parse_knowledge_check(payload.get("knowledgeCheck")),
    }


def _parse_knowledge_check(raw: Any) -> dict[str, Any] | None:
    """A check is kept only if it can actually be answered.

    Three ways a reply fails that, all of which the model does produce: no correct choice, several
    correct choices, or fewer than two options. Each would put a question on screen that cannot be
    passed, and the reader gates Continue on answering it — so a half-formed check would strand the
    learner at the end of the lesson. Dropping it lets them finish.
    """
    if not isinstance(raw, dict):
        return None

    question = _clean_str(raw.get("question"))
    if not question:
        return None

    choices = []
    for index, choice in enumerate(raw.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        label = _clean_str(choice.get("label"), 500)
        if not label:
            continue
        choices.append(
            {
                "id": _clean_str(choice.get("id"), 40) or f"choice-{index + 1}",
                "label": label,
                "correct": bool(choice.get("correct")),
            }
        )

    if len(choices) < 2 or sum(1 for c in choices if c["correct"]) != 1:
        logger.warning("Discarding knowledge check: %d choices, %d correct", len(choices), sum(1 for c in choices if c["correct"]))
        return None

    return {
        "question": question,
        "explanation": _clean_str(raw.get("explanation")) or "",
        "choices": choices,
    }


async def persist_lesson(topic_id: str, *, markdown: str, parsed: dict[str, Any]) -> int:
    """Store a generated lesson against its topic. Returns the number of sections written.

    Sections are replaced rather than appended, so regenerating a lesson does not leave the learner
    scrolling through two versions of it. The topic's own fields are written in the same call, because
    objectives that describe the previous body would be worse than none.

    `Topic.completed` is deliberately untouched. Regenerating the material a learner has already
    finished does not un-finish it for them, and flipping that flag here would silently move course
    progress as a side effect of an authoring action.
    """
    await knowledge_repo.update_topic(
        topic_id,
        {
            "content": markdown,
            "objectives": parsed.get("objectives"),
            "knowledgeCheck": parsed.get("knowledgeCheck"),
        },
    )

    await knowledge_repo.delete_topic_sections(topic_id)
    sections = parsed.get("sections") or []
    if sections:
        await knowledge_repo.create_topic_sections(topic_id, sections)
    return len(sections)


def render_markdown(title: str, parsed: dict[str, Any]) -> str:
    """Compose a readable document from the parsed lesson.

    Used when the model returned usable JSON, so that `Topic.content` holds the same lesson the
    sections describe rather than the raw reply. Study Mode reads `content` to brief the voice tutor,
    and handing it a JSON object would have the tutor reading punctuation aloud.
    """
    lines = [f"# {title}", ""]

    if objectives := parsed.get("objectives"):
        lines.append("## What you'll be able to do")
        lines.extend(f"- {objective}" for objective in objectives)
        lines.append("")

    for section in parsed.get("sections") or []:
        lines.append(f"## {section['title']}")
        lines.append("")
        if summary := section.get("summary"):
            lines.extend([f"*{summary}*", ""])
        lines.extend([paragraph + "\n" for paragraph in section["paragraphs"]])
        if key_idea := section.get("keyIdea"):
            lines.extend([f"**Key idea:** {key_idea}", ""])
        for step in section.get("steps") or []:
            lines.append(f"1. **{step['title']}** — {step['detail']}")
        if section.get("steps"):
            lines.append("")
        lines.extend(f"- {bullet}" for bullet in section.get("bullets") or [])
        if section.get("bullets"):
            lines.append("")
        if code := section.get("code"):
            lines.extend(["```", code, "```", ""])

    if check := parsed.get("knowledgeCheck"):
        lines.extend(["## Check yourself", "", check["question"], ""])
        lines.extend(f"- {choice['label']}" for choice in check["choices"])
        lines.append("")

    return "\n".join(lines).strip()
