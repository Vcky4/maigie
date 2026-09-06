"""Do banked questions actually carry a hint?

The hint feature is now reachable from the runner. `hintAvailable` is `false` when a
question has neither a nudge nor an eliminable option, so this checks how often a
learner asking for a hint would get a useful one — before the button ships and turns
out to say "No hint is available" every time.

Read-only.

    poetry run python scripts/check_hint_coverage.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
                        PrepQuestion.question_type,
                        PrepQuestion.hint_nudge,
                        PrepQuestion.options,
                        PrepQuestion.correct_answer,
                    )
                )
            ).all()

        total = len(rows)
        with_nudge = 0
        eliminable = 0
        neither = 0

        for question_type, nudge, options, correct in rows:
            has_nudge = bool(nudge and str(nudge).strip())
            # Level 2 eliminates a wrong option, which needs at least three so a
            # real choice remains.
            can_eliminate = (
                isinstance(options, list)
                and len(options) >= 3
                and any(str(o).strip().lower() != (correct or "").strip().lower() for o in options)
            )
            if has_nudge:
                with_nudge += 1
            if can_eliminate:
                eliminable += 1
            if not has_nudge and not can_eliminate:
                neither += 1

        def pct(n: int) -> str:
            return f"{(n / total) * 100:5.1f}%" if total else "   n/a"

        print(f"banked questions            : {total}")
        print(f"  with a hint nudge         : {with_nudge}  {pct(with_nudge)}")
        print(f"  level 2 can eliminate     : {eliminable}  {pct(eliminable)}")
        print(f"  no hint of any kind       : {neither}  {pct(neither)}")
        print()
        if total and with_nudge == 0:
            print(
                "FINDING: no banked question has a nudge, so a level-1 hint has nothing\n"
                "to say on any existing question. `hintNudge` is generated with new\n"
                "questions, so this affects the backlog only — but the button will look\n"
                "broken on anything generated before it existed. Level 2 still works."
            )
        elif total and with_nudge < total:
            print(
                f"FINDING: {total - with_nudge} question(s) predate `hintNudge` and will "
                "report no level-1 hint."
            )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
