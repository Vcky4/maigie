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


#: How each teaching style is described to the model.
#:
#: The wizard asks the learner how a course should be explained and stores the answer in
#: `Course.teachingStyle`. Until this existed the field was written and read by nothing, while the wizard's
#: summary panel promised "Maigie will adapt explanations and practice to your ... learning style" — a claim
#: with no mechanism behind it. This is the mechanism.
#:
#: Phrased as an instruction about emphasis rather than a format, because a style that dictated structure
#: would fight the section shape the reader needs.
_STYLE_GUIDANCE = {
    "Visual": (
        "Lead with concrete imagery, spatial descriptions and worked diagrams described in words. "
        "Prefer examples the learner can picture over formal definitions."
    ),
    "Hands-on": (
        "Lead with something the learner does. Prefer step-by-step walkthroughs and exercises over "
        "exposition, and use the `algorithm` and `example` section kinds heavily."
    ),
    "Concept first": (
        "Establish the underlying principle before any example, and name why it holds. Prefer the "
        "`concept` and `comparison` section kinds."
    ),
    "Mixed": (
        "Alternate between explanation and application so no more than two consecutive sections are of "
        "the same kind."
    ),
}


def build_lesson_prompt(
    title: str,
    existing_content: str | None = None,
    teaching_style: str | None = None,
) -> str:
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

    # The course's own style, when it has one. Appended rather than woven into the rules above so the
    # required shape stays identical for every style — a learner changing style should get different
    # emphasis, not a differently-shaped lesson the reader cannot render.
    if guidance := _STYLE_GUIDANCE.get(teaching_style or ""):
        prompt += f"\n\nThis course is taught in a {teaching_style} style. {guidance}"

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


# ---------------------------------------------------------------------------
# Course outlines
#
# The create wizard's review step showed the outline of whichever *template* the learner started from,
# regardless of what they asked for: type "Data analytics" against the public-speaking template and the
# review step offered "Working with speaking nerves" and "Read the audience". The title and category
# followed the input; the curriculum did not. Accepting it saved that outline, so the learner ended up
# with a course about the wrong subject.
#
# So the outline is generated from the brief, shown for review, and only persisted once accepted. Two
# reasons that ordering matters:
#
# - **Generation before persistence.** A rejected outline should cost nothing but a model call. Writing it
#   first and letting the learner delete it would leave partial courses behind every time they changed
#   their mind.
# - **Review before content.** An outline is a dozen titles and cheap to produce; the lessons behind them
#   are a model call each. Generating twelve lessons for an outline the learner then rejects wastes twelve
#   calls, so only the first lesson is written on acceptance and the rest are written when opened.
# ---------------------------------------------------------------------------

#: Bounds on a generated outline. A model asked for "a course" will occasionally return thirty modules of
#: one topic, which is an index rather than a curriculum.
_MAX_MODULES = 8
_MIN_MODULES = 2
_MAX_TOPICS_PER_MODULE = 10

#: The kinds a generated topic may claim, matching `TopicKind`. Anything else becomes a plain lesson rather
#: than being stored as a label the outline cannot render.
_VALID_TOPIC_KINDS = {"Lesson", "Practice", "Project", "Check"}


def build_outline_prompt(
    *,
    title: str,
    brief: str,
    level: str | None = None,
    teaching_style: str | None = None,
    category: str | None = None,
) -> str:
    """Ask for a course outline as JSON, from the learner's own brief.

    The brief is the thing being answered, so it goes in first and verbatim. Level, style and category are
    context rather than instructions — a learner who asked for something specific should get that, shaped
    by their preferences, not a generic course about their category.
    """
    prompt = f"""Design a course outline for this learner.

Their course title: "{title}"
What they asked for, in their words: "{brief}"
"""
    if category:
        prompt += f"Subject area: {category}\n"
    if level:
        prompt += f"Target level: {level}\n"
    if guidance := _STYLE_GUIDANCE.get(teaching_style or ""):
        prompt += f"Preferred teaching style: {teaching_style}. {guidance}\n"

    prompt += f"""
Return ONLY a JSON object, no prose around it:

{{
  "modules": [
    {{
      "title": "module title",
      "description": "one sentence on what this module covers",
      "topics": [
        {{
          "title": "lesson title",
          "kind": "Lesson | Practice | Project | Check",
          "durationMinutes": 15
        }}
      ]
    }}
  ],
  "outcomes": ["what the learner will be able to do afterwards"]
}}

Rules:
- Between {_MIN_MODULES} and {_MAX_MODULES} modules, ordered so each builds on the last.
- Between 2 and {_MAX_TOPICS_PER_MODULE} topics per module.
- `kind` describes what the sitting asks of the learner: reading (Lesson), rehearsing (Practice),
  building something (Project), or testing recall (Check). Most topics are Lessons. Include at least one
  Practice or Project per module where the subject allows it.
- `durationMinutes` between 5 and 90, realistic for the topic.
- Between 3 and 5 outcomes, each a concrete capability rather than a topic restated.
- Answer the learner's brief specifically. Do not produce a generic course about the subject area."""
    return prompt


def parse_outline(payload: Any) -> dict[str, Any]:
    """Turn a model reply into modules, topics and outcomes.

    Validated rather than trusted, for the same reason `parse_lesson` is: this reply is the one input to the
    feature that no type system guards. A malformed reply degrades to fewer modules, and to none if nothing
    survives — which the caller reports as a failure to generate rather than saving an empty course.

    Positions are not assigned here. The caller owns them, because the learner may reorder or drop modules
    in review before anything is written, and an order computed now would be wrong by then.
    """
    if not isinstance(payload, dict):
        return {"modules": [], "outcomes": None}

    modules: list[dict[str, Any]] = []
    for raw_module in payload.get("modules") or []:
        if not isinstance(raw_module, dict):
            continue
        module_title = _clean_str(raw_module.get("title"), 255)
        if not module_title:
            continue

        topics: list[dict[str, Any]] = []
        for raw_topic in raw_module.get("topics") or []:
            # A bare string is a shape the model does return, and it is recoverable: the title is the only
            # required field, so treat it as one rather than dropping the topic.
            if isinstance(raw_topic, str):
                raw_topic = {"title": raw_topic}
            if not isinstance(raw_topic, dict):
                continue
            topic_title = _clean_str(raw_topic.get("title"), 255)
            if not topic_title:
                continue

            kind = raw_topic.get("kind")
            duration = raw_topic.get("durationMinutes")
            topics.append(
                {
                    "title": topic_title,
                    "kind": kind if kind in _VALID_TOPIC_KINDS else "Lesson",
                    "durationMinutes": (
                        int(duration)
                        if isinstance(duration, int | float) and 5 <= duration <= 90
                        else None
                    ),
                }
            )
            if len(topics) >= _MAX_TOPICS_PER_MODULE:
                break

        # A module with no topics is a heading with nothing under it — the same reason a section with no
        # paragraphs is dropped from a lesson.
        if not topics:
            continue

        modules.append(
            {
                "title": module_title,
                "description": _clean_str(raw_module.get("description")),
                "topics": topics,
            }
        )
        if len(modules) >= _MAX_MODULES:
            break

    return {
        "modules": modules,
        "outcomes": _clean_str_list(payload.get("outcomes"), 5),
    }
