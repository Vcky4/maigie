"""Reshuffle the option order of already-banked questions.

Generation now shuffles options before persisting, but questions banked before that
still carry the model's positional bias — measured as A 44%, B 40%, C 15%, D never.
`ADAPTIVE` reuses banked questions, so without this the bias keeps being served.

**Why this is safe.** `correctAnswer` is stored as the option *text*, not an index or
a letter, so reordering the `options` array leaves the key pointing at the same
string. Nothing reads position: `_check_answer_correctness` resolves letters against
the stored order at answer time, and the client derives A/B/C/D from the array. Past
`QuizAnswer` rows keep their own `userAnswer` text and their already-decided
`isCorrect`, so history is untouched.

The one visible effect: a learner with an in-progress session containing one of these
questions will see its A/B/C/D labels change if they reload. Answers are submitted as
text, so nothing is mis-scored by that.

Dry run by default. Nothing is written without `--apply`.

    poetry run python scripts/rebalance_option_order.py
    poetry run python scripts/rebalance_option_order.py --apply
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from collections import Counter

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LETTERS = "ABCDEFGH"
# Only multiple choice. TRUE_FALSE keeps its conventional order.
TARGET_TYPE = "MULTIPLE_CHOICE"


def _position(options: list[str], correct: str) -> int | None:
    normalized = [str(o).strip().lower() for o in options]
    key = (correct or "").strip().lower()
    return normalized.index(key) if key in normalized else None


def _report(title: str, positions: Counter[int], total: int) -> None:
    print(f"\n{title}")
    for index in range(max(positions) + 1 if positions else 0):
        count = positions.get(index, 0)
        share = (count / total) * 100 if total else 0
        print(f"  {LETTERS[index]}  {count:>4}  {share:5.1f}%  {'#' * round(share / 2)}")


async def main() -> None:
    apply = "--apply" in sys.argv

    from sqlalchemy import select, update

    from src.domains.personal_learning.db_models import PrepQuestion
    from src.domains.personal_learning.services.quiz_engine import (
        balance_answer_positions,
    )
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
                    ).where(PrepQuestion.question_type == TARGET_TYPE)
                )
            ).all()

            before: Counter[int] = Counter()
            after: Counter[int] = Counter()
            batch: list[dict] = []
            ids: list[str] = []
            originals: list[list[str]] = []
            skipped = 0

            for question_id, _, options, correct in rows:
                if not isinstance(options, list) or len(options) < 2:
                    skipped += 1
                    continue
                original = _position(options, correct)
                if original is None:
                    # The key is not among its own options. Reordering would not
                    # make it answerable, and rewriting a broken row hides it.
                    skipped += 1
                    continue

                before[original] += 1
                cleaned = [str(option) for option in options]
                ids.append(question_id)
                originals.append(cleaned)
                batch.append(
                    {
                        "question_type": "MULTIPLE_CHOICE",
                        "options": list(cleaned),
                        "correct_answer": str(correct),
                    }
                )

            # The same balancing the generator uses, so the backfilled rows and the
            # newly generated ones are even in exactly the same way.
            balance_answer_positions(batch, rng=random.Random())

            planned: list[tuple[str, list[str]]] = []
            for question_id, original_options, question in zip(ids, originals, batch):
                reordered = question["options"]
                new_position = _position(reordered, question["correct_answer"])
                assert new_position is not None, "reordering lost the answer key"
                assert sorted(reordered) == sorted(original_options), "options changed"
                after[new_position] += 1
                planned.append((question_id, reordered))

            total = len(planned)
            print(f"multiple-choice questions : {len(rows)}")
            print(f"  to reshuffle            : {total}")
            print(f"  skipped                 : {skipped}")

            if not total:
                print("\nnothing to do")
                return

            _report("before:", before, total)
            _report("after:", after, total)

            if not apply:
                print("\nDRY RUN — nothing written. Re-run with --apply to write.")
                return

            for question_id, shuffled in planned:
                await session.execute(
                    update(PrepQuestion)
                    .where(PrepQuestion.id == question_id)
                    .values(options=shuffled)
                )
            await session.commit()
            print(f"\napplied to {total} questions")

        # Verify from a fresh read rather than trusting the writes.
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        PrepQuestion.id, PrepQuestion.options, PrepQuestion.correct_answer
                    ).where(PrepQuestion.question_type == TARGET_TYPE)
                )
            ).all()
            verified: Counter[int] = Counter()
            broken = 0
            for _, options, correct in rows:
                if not isinstance(options, list):
                    continue
                position = _position(options, correct)
                if position is None:
                    broken += 1
                else:
                    verified[position] += 1
            print(f"\nverified from a fresh read: {sum(verified.values())} questions")
            print(f"  keys no longer among their options: {broken}")
            _report("verified distribution:", verified, sum(verified.values()))
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
