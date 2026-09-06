"""Choosing which of a learner's material reaches the model, and how much.

Measured before writing (`scripts/check_material_usage.py`, live data): a stored
document of **162,885 characters** contributed **5,000** to topic extraction — 3.1%.
Storage was never the constraint; `_extract_upload_text` reads every page of a PDF and
nothing caps `extractedText`. Three separate consumers each sliced 5,000 characters off
the front, and they disagreed about how.

Four problems, in the order they matter:

1. **Most modes saw no material at all.** Only `PAST_PAPER_SIM` put an excerpt in the
   prompt. `QUICK_REVIEW`, `WEAK_AREAS`, `TOPIC_FOCUS`, `ADAPTIVE` and `FULL_PRACTICE`
   sent topic titles and descriptions only, so their questions came from the model's
   knowledge of the phrase "Functions and Graphs" rather than from the learner's
   document — while the workspace promised questions "tailored to your preparation"
   and the launcher had a heading reading "Written from your material".

2. **Topic extraction truncated the joined text, not each file.** It concatenated every
   material and cut the result, so a syllabus uploaded after a textbook contributed
   nothing. A syllabus is the single most useful thing to read, because it states what
   is examinable, and it is the most likely to be uploaded second.

3. **Category was ignored.** `PAST_PAPER_SIM` grounded itself in whatever was uploaded
   most recently (`createdAt DESC`), not in the material marked `PAST_QUESTION` — even
   though the whole point of collecting a category is that `SYLLABUS` and
   `PAST_QUESTION` change what a document is *for*.

4. **The budget was two orders of magnitude too conservative.** 5,000 characters is
   ~1,250 tokens: about 1% of the smallest configured context window (gpt-4o-mini at
   128k; Claude Sonnet 200k, Gemini Flash ~1M).

This module is the single answer to "what does the model get to read". It is pure —
selection and budgeting are decided here and tested here; the callers only fetch rows.

# On the budget

Raised, not removed. Latency is the reason: the first real reading of
`generation_ms` after migration `018` put quiz generation at a **p50 of 16.3s** with a
60s server timeout, so prompt size is not free. `PAST_PAPER_SIM` gets the largest share
because it is the one mode whose questions must come from the document rather than be
merely informed by it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: What each consumer may read, in characters. Roughly 4 characters per token, so the
#: largest of these is ~7.5k tokens — still under 6% of a 128k window, which leaves
#: room for the instructions, the topic list and the response.
#:
#: Chosen against the measured latency rather than against the context limit. Raise
#: these only alongside a fresh `check_generation_latency.py` reading.
TOPIC_EXTRACTION_BUDGET = 24_000
PAST_PAPER_BUDGET = 30_000
#: Grounding for the ordinary practice modes. Smaller on purpose: these questions are
#: *informed* by the material, and the topic list already narrows what to ask about.
QUESTION_GROUNDING_BUDGET = 12_000

#: No single file may take more than this share of a budget, so one long textbook
#: cannot crowd out the syllabus sitting behind it.
MAX_SHARE_PER_FILE = 0.6

#: Nothing shorter than this is worth a slot — a 200-character fragment costs a file
#: its place while telling the model almost nothing.
MIN_USEFUL_CHARS = 200

#: Read first, whatever the upload order. A syllabus states what is examinable, and
#: past questions show how it is asked; both are worth more per character than a
#: textbook. Anything not listed keeps its relative order after these.
CATEGORY_PRIORITY: dict[str, int] = {
    "SYLLABUS": 0,
    "PAST_QUESTION": 1,
    "NOTES": 2,
    "SLIDE": 3,
    "TEXTBOOK": 4,
    "LINK": 5,
    "OTHER": 6,
}
_UNKNOWN_CATEGORY_RANK = 7


@dataclass(frozen=True)
class MaterialExcerpt:
    """One file's contribution to a prompt."""

    filename: str
    category: str
    text: str
    #: Length of the stored text, so a caller can report how much was left unread.
    stored_chars: int

    @property
    def truncated(self) -> bool:
        return len(self.text) < self.stored_chars


@dataclass(frozen=True)
class MaterialContext:
    """What the model will read, and what it will not."""

    excerpts: list[MaterialExcerpt]
    stored_chars: int
    used_chars: int
    #: Files with readable text that got no slot at all. Worth logging: it is the
    #: signal that a budget is too small for what the learner uploaded.
    omitted: list[str]

    @property
    def has_text(self) -> bool:
        return bool(self.excerpts)

    def as_prompt_block(self) -> str:
        """The excerpts, labelled by filename and category.

        The category is included because it tells the model what the document *is*:
        a syllabus defines scope, a past paper demonstrates question style, and a
        textbook supplies facts. Without it they are an undifferentiated wall of text.
        """
        return "\n\n".join(
            f"[{excerpt.category} · {excerpt.filename}]\n{excerpt.text}"
            for excerpt in self.excerpts
        )


def _rank(material: Any) -> int:
    category = (getattr(material, "category", None) or "OTHER").upper()
    return CATEGORY_PRIORITY.get(category, _UNKNOWN_CATEGORY_RANK)


def select(
    materials: Sequence[Any],
    *,
    budget: int,
    categories: Sequence[str] | None = None,
) -> MaterialContext:
    """Choose and trim material to fit `budget`.

    `categories`, when given, restricts the pool — used by exam simulation so a paper
    is built from past questions rather than from whatever was uploaded last.

    The budget is shared rather than consumed front-to-back. Each file is capped at
    `MAX_SHARE_PER_FILE` of what remains for it, so the second and third documents
    always get a slot when they have anything to say.
    """
    pool = [m for m in materials if (getattr(m, "extracted_text", None) or "").strip()]

    if categories:
        wanted = {c.upper() for c in categories}
        filtered = [m for m in pool if (getattr(m, "category", None) or "OTHER").upper() in wanted]
        # Falling back to everything is deliberate. A learner who uploaded a paper
        # without labelling it should still get a grounded simulation; refusing would
        # punish them for a category they were never required to set.
        pool = filtered or pool

    stored_chars = sum(len(m.extracted_text or "") for m in pool)
    if not pool or budget <= 0:
        return MaterialContext(excerpts=[], stored_chars=stored_chars, used_chars=0, omitted=[])

    # Highest-value category first, then most recent, which is the order the
    # repository already returns within a category.
    ordered = sorted(enumerate(pool), key=lambda pair: (_rank(pair[1]), pair[0]))

    excerpts: list[MaterialExcerpt] = []
    omitted: list[str] = []
    remaining = budget

    for position, (_, material) in enumerate(ordered):
        text = (material.extracted_text or "").strip()
        files_left = len(ordered) - position

        if remaining < MIN_USEFUL_CHARS:
            omitted.append(material.filename)
            continue

        # Reserve a minimum for each file still to come, then cap this file's share of
        # what is left. Both bounds matter: the reservation is what stops file one
        # from starving file two, and the share cap is what stops a long file from
        # taking everything the reservation left.
        reserved = min(remaining, MIN_USEFUL_CHARS * (files_left - 1))
        spendable = remaining - reserved
        allowance = max(MIN_USEFUL_CHARS, int(spendable * MAX_SHARE_PER_FILE))
        # The last file may use everything left; there is nobody to reserve for.
        if files_left == 1:
            allowance = remaining

        take = min(len(text), allowance, remaining)
        if take < MIN_USEFUL_CHARS and len(text) >= MIN_USEFUL_CHARS:
            omitted.append(material.filename)
            continue

        excerpts.append(
            MaterialExcerpt(
                filename=material.filename,
                category=(getattr(material, "category", None) or "OTHER").upper(),
                text=text[:take],
                stored_chars=len(text),
            )
        )
        remaining -= take

    return MaterialContext(
        excerpts=excerpts,
        stored_chars=stored_chars,
        used_chars=sum(len(e.text) for e in excerpts),
        omitted=omitted,
    )
