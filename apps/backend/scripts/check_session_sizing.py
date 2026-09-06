"""What length each practice mode would actually produce, per preparation.

Reported as "why are they all 5 questions": the client sent a hardcoded 5 on every
request, so the server's own sizing never ran. This shows what the server would
choose, which is what the launcher now displays.

Read-only.

    poetry run python scripts/db_direct.py python scripts/check_session_sizing.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODES = ("QUICK_REVIEW", "WEAK_AREAS", "TOPIC_FOCUS", "PAST_PAPER_SIM", "ADAPTIVE")
FOCUS_THRESHOLD = 70.0


async def main() -> None:
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import prep_focus, quiz_engine
    from src.shared.database.session import connect_db, disconnect_db

    await connect_db()
    try:
        candidates = await repo.list_snapshot_candidate_preps(skip=0, take=4)
        if not candidates:
            print("no preparations to read")
            return

        for prep in await repo.list_exam_preps_by_ids([c.id for c in candidates]):
            topics = await repo.list_prep_topics(prep.id)
            weak = [t for t in topics if (t.mastery_score or 0.0) < FOCUS_THRESHOLD]
            materials = await repo.list_prep_materials(prep.id)
            readable = [m for m in materials if m.extracted_text]

            print(
                f"\n{prep.subject}  ({len(topics)} topics, {len(weak)} below 70%, "
                f"{len(readable)} readable file{'' if len(readable) == 1 else 's'})"
            )
            for mode in MODES:
                if mode == "TOPIC_FOCUS":
                    target = 1
                elif mode == "WEAK_AREAS":
                    target = len(weak) or len(topics)
                else:
                    target = len(topics)
                count = quiz_engine.default_question_count(mode, target)
                minutes = quiz_engine.estimated_minutes(count)
                note = ""
                if mode == "PAST_PAPER_SIM" and not readable:
                    note = "  <- blocked: PREP_MATERIAL_REQUIRED"
                print(f"  {mode:<16} {count:>3} questions  ~{minutes:>3} min{note}")

            counts = await repo.get_prep_topic_question_counts([prep.id])
            answered = {tid: c.get("answered_count", 0) for tid, c in counts.items()}
            recommendation = prep_focus.recommend(topics, answered_by_topic=answered)
            print(
                f"  recommended      {recommendation.recommended_mode} "
                f"x{recommendation.recommended_question_count} "
                f"~{recommendation.estimated_minutes} min "
                f"({recommendation.reason_code})"
            )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
