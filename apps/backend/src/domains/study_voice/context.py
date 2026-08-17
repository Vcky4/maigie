"""The tutor brief — everything the model is told before the learner speaks.

This is composed server-side from data we already hold, and it is the difference between a voice session
and a generic chatbot with a microphone. The provider is given one system instruction at setup and it
cannot be replaced on a live connection, so whatever is missing here is missing for the whole session.

## Extended from the original, deliberately

The deleted implementation grounded the brief on the topic's **notes** and **resources**, which this keeps —
that was better than the rewrite proposed in the design document, and it is the only place uploaded material
reaches the tutor. What it could not include is everything added to `Topic` since: `objectives`, real
`TopicSection` rows, `knowledgeCheck`, and the check attempts recorded by the lesson reader. Those are added
here, per §11.1 of `docs/VOICE_STUDY_SESSION_DESIGN.md`.

The most valuable of them is the last one. A learner who got the end-of-lesson check wrong and then changed
their answer is recorded as having needed a second go, and a tutor that opens by returning to exactly that
is the product this feature is for.

## Two things withheld on purpose

- **The correct answer to the knowledge check.** The question goes in so the tutor can probe it; the answer
  does not, because a tutor holding the key tends to give it away, and the check is the one place the
  learner's understanding is measured rather than assisted.
- **Anything the client sent.** `system_instruction` arrives in the start request from both shipped clients
  and is ignored. A browser-authored system prompt is a browser-authored tutor: it can ask for the answer
  key, drop the safety framing, or instruct the model to bill-free chat about anything. The field stays in
  the request model because removing it would break two clients for no gain, and its docstring says so.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domains.knowledge.repository import knowledge_repo
from src.domains.knowledge.services import topic_check_service
from src.domains.knowledge.services.course_service import check_topic_ownership
from src.domains.knowledge.services.lesson_service import style_guidance
from src.domains.personal_learning.repository import personal_learning_repo

logger = logging.getLogger(__name__)

#: How the tutor behaves regardless of what is being taught. Spoken-first, because everything it says is
#: read aloud: no markdown, no lists it would have to pronounce, and diagrams pushed to the screen through
#: `study_show_visual` rather than described as syntax.
BASE_INSTRUCTION = (
    "You are Maigie, a warm and knowledgeable study tutor talking with one learner by voice.\n"
    "\n"
    "How to speak:\n"
    "- Everything you say is spoken aloud. Never use markdown, bullet characters, headings or emoji.\n"
    "- Keep turns short — two or three sentences — then stop and let the learner respond.\n"
    "- Ask questions. A tutor that only explains is a podcast.\n"
    "- When the learner is wrong, say so plainly and kindly, then work out why with them.\n"
    "- Never claim to have done something you have not done.\n"
    "- If you need a diagram or an equation, call the study_show_visual tool. It appears on the learner's "
    "screen straight away and is kept on the lesson afterwards, so you can refer to it as something they "
    "can see. Do not read diagram syntax or LaTeX out loud.\n"
    "- When the learner has finished a topic and wants to move on, call the "
    "complete_topic_and_continue tool. Saying that you have marked it complete does not mark it "
    "complete; only the tool does, and telling them otherwise breaks the rule above about never "
    "claiming to have done something you have not.\n"
)

_NO_TOPIC_INSTRUCTION = "\nThe learner has not opened a specific lesson, so ask what they want to work on before teaching."

_MAX_NOTE_CHARS = 12000
_MAX_SECTIONS = 40
_MAX_RESOURCES = 10


async def build_brief(
    user_id: str,
    *,
    course_id: str | None,
    topic_id: str | None,
) -> str:
    """Compose the system instruction for a session.

    Raises `NotFoundError` / `ForbiddenError` when the topic is not this learner's, through the same
    `check_topic_ownership` every other topic read uses. The original never read the topic at all, so it
    never had to check; now that the brief quotes lesson content, an unchecked id would read another
    learner's material aloud.
    """
    if not topic_id:
        return BASE_INSTRUCTION + _NO_TOPIC_INSTRUCTION

    topic, _module, course = await check_topic_ownership(topic_id, user_id)

    parts: list[str] = [BASE_INSTRUCTION]

    parts.append(
        "\nWhat this session is about:\n" f"- Course: {course.title}\n" f"- Lesson: {topic.title}"
    )
    if topic.summary:
        parts.append(f"- In one line: {topic.summary}")

    if guidance := style_guidance(course.teaching_style):
        parts.append(
            f"\nThis learner asked to be taught in a {course.teaching_style} way. {guidance}"
        )

    if objectives := _objective_lines(topic.objectives):
        parts.append("\nBy the end of this lesson the learner should be able to:\n" + objectives)

    sections = await _section_outline(topic_id)
    if sections:
        parts.append(
            "\nThe written lesson covers these sections, in order. Teach around them rather than "
            "reciting them, and do not re-teach what is already marked read:\n" + sections
        )

    if check_block := await _check_block(topic_id, user_id, topic.knowledge_check):
        parts.append(check_block)

    if notes := await _notes_block(topic_id, user_id):
        parts.append(notes)

    if resources := await _resources_block(topic_id, user_id, course_id):
        parts.append(resources)

    return "\n".join(parts)


def _objective_lines(objectives: Any) -> str:
    if not isinstance(objectives, list):
        return ""
    lines = [f"- {str(o).strip()}" for o in objectives if str(o or "").strip()]
    return "\n".join(lines)


async def _section_outline(topic_id: str) -> str:
    """Section titles, summaries and key ideas — the lesson's real shape.

    Titles and one line each rather than the full body: the whole lesson would crowd out everything else
    in the brief, and the tutor's job is to talk about the material, not to read it back.
    """
    try:
        sections = await knowledge_repo.list_topic_sections(topic_id)
    except Exception as exc:
        logger.warning("Could not load sections for voice brief on topic %s: %s", topic_id, exc)
        return ""

    lines: list[str] = []
    for section in sections[:_MAX_SECTIONS]:
        marker = "[read]" if section.completed else "[not yet read]"
        line = f"- {marker} {section.title}"
        detail = (section.summary or section.key_idea or "").strip()
        if detail:
            line += f" — {detail}"
        lines.append(line)
    return "\n".join(lines)


async def _check_block(topic_id: str, user_id: str, check: dict | None) -> str:
    """The check question, and how the learner has fared on it. Never the answer."""
    question = topic_check_service.question_of(check)
    if not question:
        return ""

    block = (
        "\nThe lesson ends with this question, which the learner has to answer on their own:\n"
        f'- "{question}"\n'
        "You know the question but not the answer. Probe their understanding of it; never state or hint "
        "at which option is correct."
    )

    try:
        attempts = await knowledge_repo.list_topic_check_attempts(topic_id, user_id)
    except Exception as exc:
        logger.warning(
            "Could not load check attempts for voice brief on topic %s: %s", topic_id, exc
        )
        return block

    summary = topic_check_service.summarise(attempts)
    if not summary.attempts:
        return block + "\nThey have not attempted it yet."

    if summary.needs_revisit:
        block += (
            f"\nThey have answered it {summary.attempts} time(s) and got it wrong "
            f"{summary.incorrect_attempts} time(s)"
            + (", though they did reach the right answer eventually" if summary.passed else "")
            + ". This is the part to spend the session on. Bring it up early and gently."
        )
    else:
        block += "\nThey answered it correctly first time, so you can move faster here."
    return block


async def _notes_block(topic_id: str, user_id: str) -> str:
    """The learner's own notes on this topic, oldest first."""
    try:
        notes, _total = await personal_learning_repo.list_notes(
            user_id,
            where={"topicId": topic_id, "archived": False},
            take=20,
        )
    except Exception as exc:
        logger.warning("Could not load notes for voice brief on topic %s: %s", topic_id, exc)
        return ""

    # `list_notes` returns most-recently-updated first; read them in the order they were written so the
    # tutor sees the learner's thinking develop rather than in reverse.
    bodies = [(n.content or "").strip() for n in reversed(notes) if (n.content or "").strip()]
    if not bodies:
        return ""

    blob = "\n\n---\n\n".join(bodies)
    if len(blob) > _MAX_NOTE_CHARS:
        blob = blob[-_MAX_NOTE_CHARS:]
    return (
        "\nThese are the learner's own notes on this topic. Refer to them by what they say, and correct "
        "them if they contain a mistake:\n" + blob
    )


async def _resources_block(topic_id: str, user_id: str, course_id: str | None) -> str:
    """Material attached to this topic, with anything the learner supplied listed first.

    The split matters: a resource the learner uploaded is material they chose, and grounding on it is
    grounding on their course. An AI recommendation is a link we suggested, which the tutor should know
    about but not treat as source material.
    """
    try:
        resources, _total = await knowledge_repo.list_resources(
            where={"userId": user_id, "topicId": topic_id},
            take=30,
            order={"updatedAt": "desc"},
        )
    except Exception as exc:
        logger.warning("Could not load resources for voice brief on topic %s: %s", topic_id, exc)
        return ""

    if not resources:
        return ""

    learner_supplied = [r for r in resources if not _is_ai_recommendation(r)]
    block = ""
    if learner_supplied:
        block += "\nThe learner attached these to the topic themselves — prioritise them for grounding:\n"
        block += "\n".join(_resource_line(r) for r in learner_supplied[:_MAX_RESOURCES])
    others = [r for r in resources if _is_ai_recommendation(r)]
    if others:
        block += "\nAlso linked to this topic, as suggestions rather than source material:\n"
        block += "\n".join(_resource_line(r) for r in others[:_MAX_RESOURCES])
    return block


def _is_ai_recommendation(resource: Any) -> bool:
    if str(getattr(resource, "recommendation_source", "") or "").lower() == "ai":
        return True
    meta = getattr(resource, "metadata_json", None)
    return isinstance(meta, dict) and meta.get("studioAiRecommendation") is True


def _resource_line(resource: Any) -> str:
    rtype = str(getattr(resource, "type", "OTHER") or "OTHER").upper()
    title = (getattr(resource, "title", "") or "Untitled").strip()
    url = (getattr(resource, "url", "") or "").strip()
    description = (getattr(resource, "description", "") or "").strip()
    if len(description) > 140:
        description = description[:140] + "..."
    line = f"- [{rtype}] {title}"
    if url:
        line += f" ({url})"
    if description:
        line += f" — {description}"
    return line
