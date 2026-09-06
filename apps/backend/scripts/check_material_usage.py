"""How much of a learner's uploaded material actually reaches the model?

Extraction stores the **whole** document — `_extract_upload_text` reads every page of
a PDF and there is no cap on `extractedText`. Every consumer then slices 5,000
characters off the front. This reports the gap between what was stored and what is
used, per preparation.

Two properties of that slice matter more than its size:

- **Topic extraction concatenates all materials, then truncates the concatenation.**
  So a syllabus uploaded after a textbook can be entirely invisible.
- **Materials are ordered `createdAt DESC`**, so "the front" means the most recent
  upload, not the most important one.

Read-only.

    poetry run python scripts/db_direct.py python scripts/check_material_usage.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Read from the module that owns them, so this script cannot drift from the budgets
# actually in force — which is how three consumers came to disagree in the first place.
from src.domains.personal_learning.services import prep_material_context as _ctx  # noqa: E402

TOPIC_EXTRACTION_CHARS = _ctx.TOPIC_EXTRACTION_BUDGET
PAST_PAPER_EXCERPT_CHARS = _ctx.PAST_PAPER_BUDGET
QUESTION_GROUNDING_CHARS = _ctx.QUESTION_GROUNDING_BUDGET

# Rough, and only used to put the cap in proportion. Enough for an order of magnitude.
CHARS_PER_TOKEN = 4
# Smallest context window among the configured providers (gpt-4o-mini). Claude Sonnet
# is 200k and Gemini Flash is ~1M, so this is the conservative bound.
SMALLEST_CONTEXT_TOKENS = 128_000


async def main() -> None:
    from sqlalchemy import select

    from src.domains.personal_learning.db_models import ExamPrep, PrepMaterial
    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            preps = {
                row.id: row.subject
                for row in (await session.execute(select(ExamPrep.id, ExamPrep.subject))).all()
            }
            materials = (
                await session.execute(
                    select(
                        PrepMaterial.prep_id,
                        PrepMaterial.filename,
                        PrepMaterial.category,
                        PrepMaterial.extracted_text,
                        PrepMaterial.created_at,
                    ).order_by(PrepMaterial.created_at.desc())
                )
            ).all()

        print(
            f"caps: topic extraction {TOPIC_EXTRACTION_CHARS:,} chars, "
            f"past-paper excerpt {PAST_PAPER_EXCERPT_CHARS:,} chars"
        )
        print(
            f"      {TOPIC_EXTRACTION_CHARS // CHARS_PER_TOKEN:,} tokens, roughly "
            f"{(TOPIC_EXTRACTION_CHARS // CHARS_PER_TOKEN) / SMALLEST_CONTEXT_TOKENS:.1%} "
            f"of the smallest configured context window"
        )
        print()

        if not materials:
            print("No materials stored, so nothing to measure yet.")
            print()
            print(
                "What the caps mean once there are:\n"
                "  - a 10-page PDF holds roughly 20,000 chars, so ~25% would be used\n"
                "  - a 300-page textbook holds roughly 600,000 chars, so ~0.8% would be used\n"
                "  - with several materials, topic extraction truncates the *joined*\n"
                "    text, so later files in the ordering contribute nothing at all"
            )
            return

        by_prep: dict[str, list] = {}
        for row in materials:
            by_prep.setdefault(row.prep_id, []).append(row)

        print(f"{'preparation':<26}{'files':>6}{'stored':>12}{'used':>10}{'share':>8}")
        print("-" * 62)
        starved: list[tuple[str, str]] = []
        for prep_id, rows in by_prep.items():
            subject = (preps.get(prep_id) or prep_id)[:24]
            texts = [(r.filename, r.category, (r.extracted_text or "")) for r in rows]
            stored = sum(len(text) for _, _, text in texts)
            if stored == 0:
                print(f"{subject:<26}{len(rows):>6}{0:>12}{0:>10}{'n/a':>8}")
                continue

            # Ask the real selector, so this reports what will happen rather than a
            # re-implementation of it.
            selected = _ctx.select(rows, budget=TOPIC_EXTRACTION_CHARS)
            unseen = list(selected.omitted)
            used = selected.used_chars
            print(f"{subject:<26}{len(rows):>6}{stored:>12,}{used:>10,}{used / stored:>7.1%}")
            for filename in unseen:
                print(f"{'':<26}  never reaches extraction: {filename}")
                starved.append((subject, filename))

        print()
        if starved:
            print(
                f"FINDING: {len(starved)} material(s) contribute nothing to topic extraction.\n"
                "Extraction joins every material and truncates the join, so a file behind\n"
                "the cap is invisible — including a syllabus, which is the material most\n"
                "worth reading and the one most likely to be uploaded second."
            )
        print(
            f"Every mode now grounds in material: {QUESTION_GROUNDING_CHARS:,} chars for\n"
            f"ordinary practice, {PAST_PAPER_EXCERPT_CHARS:,} for exam simulation, which also\n"
            "restricts the pool to PAST_QUESTION and SYLLABUS when either is labelled."
        )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
