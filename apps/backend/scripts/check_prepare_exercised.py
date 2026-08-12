"""Which Prepare capabilities have ever actually run against real data?

Every phase of the Prepare plan was verified by tests and by a read-only check of the
data it reads. This asks the question those checks do not: for each capability that
*writes* something, has anything ever been written?

The distinction matters because this plan's recurring defect was a capability that
existed, passed its tests, and had never been exercised — hints, the timeline, plan
generation, material relabelling. A table at zero rows is the same signal one level
down: the code path is reachable now, and nothing has gone through it.

Read-only.

    poetry run python scripts/db_direct.py python scripts/check_prepare_exercised.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (label, SQL, the capability it evidences, what a zero means)
PROBES: list[tuple[str, str, str, str]] = [
    (
        "materials uploaded",
        'SELECT count(*) FROM "PrepMaterial"',
        "Phase 4c multipart upload + Phase 4k materials panel",
        "no learner has ever successfully stored a file; topic extraction has only "
        "ever run from the subject line, and PAST_PAPER_SIM cannot work at all",
    ),
    (
        "  of those, with readable text",
        'SELECT count(*) FROM "PrepMaterial" WHERE "extractedText" IS NOT NULL',
        "server-side text extraction",
        "nothing readable exists, so `hasExtractedText` is false everywhere",
    ),
    (
        "readiness snapshots captured",
        'SELECT count(*) FROM "PrepReadinessSnapshot"',
        "Phase 4b.5 daily Celery writer at 00:30 UTC",
        "the scheduled task has never written a row, so the readiness trend chart "
        "has no history to draw and `targetPercent` from migration 016 is unused",
    ),
    (
        "question flags",
        'SELECT count(*) FROM "PrepQuestionFlag"',
        "Phase 4b.4 flagging",
        "nobody has flagged a question, so the `flaggedOnly` filter is unexercised",
    ),
    (
        "practice observations",
        'SELECT count(*) FROM "PracticeObservation"',
        "Phase A observation record",
        "no evidence has been captured, so the competence estimate has nothing to "
        "read and every topic is unmeasurable",
    ),
    (
        "study plans",
        'SELECT count(*) FROM "StudyPlan" WHERE "prepId" IS NOT NULL',
        "Phase 4j plan generation",
        "no preparation has a timeline",
    ),
    (
        "  scheduled adaptively",
        "SELECT count(*) FROM \"StudyPlan\" WHERE strategy = 'ADAPTIVE'",
        "Phase 4l adaptive scheduling",
        "`prep_plan_adaptive` has never produced a real plan — unit-tested, " "never exercised",
    ),
    (
        "  superseded by a regenerate",
        "SELECT count(*) FROM \"StudyPlan\" WHERE status = 'SUPERSEDED'",
        "Phase 4j supersede-on-regenerate",
        "nobody has rebuilt a plan, so the branch that prevents duplicated "
        "timelines has never run",
    ),
    (
        "quiz sessions with a timing",
        'SELECT count(*) FROM "QuizSession" WHERE "generationMs" IS NOT NULL',
        "Phase 4l generation instrumentation",
        "no session has started since migration 018, so Decision H's p95 is still "
        "unanswerable in practice",
    ),
    (
        "questions with a hint nudge",
        'SELECT count(*) FROM "PrepQuestion" WHERE "hintNudge" IS NOT NULL',
        "Phase 4h hints",
        "every hint request falls back to option elimination",
    ),
    (
        "answers recorded",
        'SELECT count(*) FROM "QuizAnswer"',
        "the practice runner",
        "nothing has been practised, so every readiness figure is 0 by definition",
    ),
]


async def main() -> None:
    from sqlalchemy import text

    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            results: list[tuple[str, int, str, str]] = []
            for label, query, capability, meaning in PROBES:
                count = (await session.execute(text(query))).scalar_one()
                results.append((label, count, capability, meaning))

            print(f"{'':<34}{'rows':>6}")
            print("-" * 42)
            for label, count, _, _ in results:
                marker = "  <-- never exercised" if count == 0 else ""
                print(f"{label:<34}{count:>6}{marker}")

            unexercised = [(label, cap, why) for label, count, cap, why in results if count == 0]
            if not unexercised:
                print("\nEvery write path above has been exercised at least once.")
                return

            print(f"\n{len(unexercised)} capability path(s) have never run:\n")
            for label, capability, why in unexercised:
                print(f"  {label.strip()}")
                print(f"    built by : {capability}")
                print(f"    so       : {why}")
                print()
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
