"""Where does the correct answer sit among a question's options?

Reported from real use: "seems all answers are option B". Language models have a
well-documented positional bias, and the generation prompt says nothing about where
the correct option should go — so this checks the banked questions rather than
assuming.

A learner who can score full marks by always picking B has learned nothing, and the
score measures nothing, so the distribution here decides whether that is happening.

Read-only.

    poetry run python scripts/check_option_positions.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LETTERS = "ABCDEFGH"


async def main() -> None:
    from sqlalchemy import select

    from src.domains.personal_learning.db_models import PrepQuestion
    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        PrepQuestion.id,
                        PrepQuestion.question_type,
                        PrepQuestion.options,
                        PrepQuestion.correct_answer,
                    )
                )
            ).all()

        positions: Counter[str] = Counter()
        unresolved = 0
        non_choice = 0
        total = 0

        for _, question_type, options, correct in rows:
            if (question_type or "").upper() not in ("MULTIPLE_CHOICE", "TRUE_FALSE"):
                non_choice += 1
                continue
            if not isinstance(options, list) or not options:
                non_choice += 1
                continue

            total += 1
            normalized = [str(o).strip().lower() for o in options]
            key = (correct or "").strip().lower()
            if key in normalized:
                index = normalized.index(key)
                positions[LETTERS[index] if index < len(LETTERS) else str(index)] += 1
            else:
                # The key is not one of its own options: unanswerable, and exactly
                # what Phase 4 validation rejects at generation.
                unresolved += 1

        print(f"choice questions examined : {total}")
        print(f"  key not among options   : {unresolved}")
        print(f"  skipped (not choice)    : {non_choice}")

        if not total:
            print("\nno choice questions to report on")
            return

        print("\ncorrect answer position:")
        for letter in LETTERS:
            count = positions.get(letter, 0)
            if count == 0 and letter not in positions:
                continue
            share = (count / total) * 100
            bar = "#" * round(share / 2)
            print(f"  {letter}  {count:>4}  {share:5.1f}%  {bar}")

        top_letter, top_count = positions.most_common(1)[0] if positions else ("-", 0)
        top_share = (top_count / total) * 100
        print()
        # With four options an unbiased generator lands near 25% per position.
        print(f"most common position      : {top_letter} at {top_share:.1f}%")
        if top_share >= 50:
            print(
                "VERDICT: badly skewed. A learner can score well by always picking "
                f"{top_letter}, so the score does not measure knowledge."
            )
        elif top_share >= 35:
            print("VERDICT: skewed enough to be worth correcting.")
        else:
            print("VERDICT: roughly even.")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
