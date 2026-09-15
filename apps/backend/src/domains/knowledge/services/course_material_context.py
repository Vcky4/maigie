"""Turn a course's uploaded material into something an outline prompt can read.

A learner who attaches a syllabus to a course is telling us what the course should cover.
Until `Resource.extractedText` existed, that file was a download link and the outline was
designed from the brief alone — so the most authoritative input available was the one input
ignored.

This module is deliberately thin. The hard part — choosing which files get prompt space,
sharing a character budget between them so the second document is not starved by the first,
and labelling excerpts so the model knows what it is reading — is already solved by
`personal_learning.services.prep_material_context`, which preparation topic extraction and
question grounding both use. Reimplementing that for courses would give two budgeters to keep
in step, and the one that saw less traffic would be the one that quietly regressed.

The only genuine difference is shape. A `PrepMaterial` has `filename` and `category`; a
`Resource` has a `title` and no category at all, because a resource is "something worth
reading attached to a course" and nothing in the course flow asks the learner to classify an
upload. `_MaterialView` bridges that gap, mapping title to filename and reporting every course
upload as `SYLLABUS` — not a guess about the file, but a statement about the role it plays
here: it is the document defining scope for the thing being generated, which is exactly what
`CATEGORY_PRIORITY` ranks first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.domains.personal_learning.services import prep_material_context

from ..repository import knowledge_repo

logger = logging.getLogger(__name__)

#: An outline is a page of titles, so it needs far less grounding than topic extraction, which
#: is reconstructing a syllabus. Two thirds of the extraction budget leaves comfortable room
#: for the brief, the style guidance and the JSON contract in the same prompt.
OUTLINE_BUDGET = 16_000


@dataclass(frozen=True)
class _MaterialView:
    """A `Resource` seen through the interface `prep_material_context.select` expects."""

    extracted_text: str | None
    filename: str
    category: str


def _view(resource: Any) -> _MaterialView:
    return _MaterialView(
        extracted_text=getattr(resource, "extracted_text", None),
        filename=getattr(resource, "title", None) or "material",
        category="SYLLABUS",
    )


async def for_course(
    *, course_id: str, user_id: str, budget: int = OUTLINE_BUDGET
) -> prep_material_context.MaterialContext:
    """Material context for a course, empty when nothing readable is attached.

    An empty context is the normal case — most courses have no uploads — and callers are
    expected to branch on `has_text` rather than treat it as a failure.
    """
    resources = await knowledge_repo.list_course_materials_with_text(course_id, user_id)
    context = prep_material_context.select([_view(r) for r in resources], budget=budget)
    if context.omitted:
        # The signal that a learner uploaded more than an outline prompt can hold. Worth a line,
        # because the alternative is a grounded-looking outline that ignored half the syllabus.
        logger.info(
            "Course material omitted from outline prompt",
            extra={"courseId": course_id, "omitted": context.omitted},
        )
    return context


def as_prompt_material(context: prep_material_context.MaterialContext) -> str | None:
    """The prompt block, or `None` when there is nothing to ground with."""
    return context.as_prompt_block() if context.has_text else None
