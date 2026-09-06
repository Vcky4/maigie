"""Does starting a quiz now return fast, and do the stages actually advance?

The point of backgrounding generation is that the POST stops blocking for the measured
p50 of 16.3s. This drives the real code path against the real database and the real
provider, printing how long the "request" half took and every stage transition observed
afterwards.

**Writes.** It creates one quiz session on a preparation that already has topics, the
same as a learner pressing start. It does not delete it, because a session is a
learner's record and quietly removing one would be worse than leaving it.

    poetry run python scripts/db_direct.py python scripts/smoke_generation_stages.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLL_SECONDS = 0.75
GIVE_UP_SECONDS = 120


async def main() -> None:
    from sqlalchemy import func, select

    from src.domains.personal_learning.db_models import ExamPrep, PrepTopic
    from src.domains.personal_learning.services import quiz_engine
    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            # A preparation with topics, since generation refuses without them.
            row = (
                await session.execute(
                    select(ExamPrep.id, ExamPrep.user_id, ExamPrep.subject)
                    .join(PrepTopic, PrepTopic.prep_id == ExamPrep.id)
                    .group_by(ExamPrep.id, ExamPrep.user_id, ExamPrep.subject)
                    .having(func.count(PrepTopic.id) > 0)
                    .limit(1)
                )
            ).first()

        if row is None:
            print("No preparation with topics, so there is nothing to generate against.")
            return

        prep_id, user_id, subject = row
        print(f"preparation : {subject} ({prep_id})")

        # Warm the pool first. A running server has connections already open, so timing
        # the first call after `connect_db` would charge the request for setup a learner
        # never pays and overstate the figure this script exists to report.
        from src.domains.personal_learning.repository import personal_learning_repo as repo

        await repo.count_prep_topics(prep_id)
        print()

        started = time.perf_counter()
        quiz = await quiz_engine.start_quiz(
            user_id=user_id, prep_id=prep_id, mode="QUICK_REVIEW", question_count=5
        )
        request_ms = (time.perf_counter() - started) * 1000

        quiz_id = quiz["id"]
        print(f"POST returned in {request_ms:.0f} ms")
        print(f"  status         : {quiz['status']}")
        print(f"  stage          : {quiz['generation_stage']}")
        print(f"  questions      : {len(quiz['questions'])}")
        print()
        if request_ms > 5_000:
            print(
                "FINDING: the request half is still slow. Backgrounding only moved the\n"
                "LLM call; if this stays high, the validation reads before session\n"
                "creation are the next thing to look at."
            )
            print()

        print("stages observed while polling:")
        seen: list[str] = []
        deadline = time.perf_counter() + GIVE_UP_SECONDS
        final = None
        while time.perf_counter() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            current = await quiz_engine.get_quiz(user_id=user_id, quiz_id=quiz_id)
            stage = current["generation_stage"]
            elapsed = time.perf_counter() - started
            if stage and (not seen or seen[-1] != stage):
                seen.append(stage)
                progress = current["generation_progress"]
                print(f"  {elapsed:6.1f}s  {stage:<20} progress={progress}")
            if current["status"] != "GENERATING":
                final = current
                print(f"  {elapsed:6.1f}s  status -> {current['status']}")
                break

        print()
        if final is None:
            print(
                f"Still GENERATING after {GIVE_UP_SECONDS}s. The staleness bound is\n"
                f"{quiz_engine.GENERATION_TIMEOUT_SECONDS}s, so a later read will mark it FAILED."
            )
            return

        print(f"questions   : {len(final['questions'])}")
        print(f"total time  : {time.perf_counter() - started:.1f}s")
        print(f"stages seen : {' -> '.join(seen) or 'none'}")
        print()
        # The whole justification for the change: the wait is no longer inside the
        # request, so the client can render the stages above.
        print(
            f"The learner waited {request_ms:.0f} ms for a response and watched "
            f"{len(seen)} real stage(s)\nfor the rest, instead of "
            f"{time.perf_counter() - started:.0f}s of an indeterminate bar."
        )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
