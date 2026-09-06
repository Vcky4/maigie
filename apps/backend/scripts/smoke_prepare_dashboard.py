"""Read-only check of what the Prepare dashboard actually returns.

The practice entry page and the `/prepare` dashboard both render from this one
payload, so this shows what a learner will really see — including whether the
per-preparation recommendation resolves, which is what the entry page's "next
priority" and recommended set size come from.

Writes nothing.

    poetry run python scripts/db_direct.py python scripts/smoke_prepare_dashboard.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import prepare_dashboard_service
    from src.shared.database.session import connect_db, disconnect_db

    await connect_db()
    try:
        candidates = await repo.list_snapshot_candidate_preps(skip=0, take=1)
        if not candidates:
            print("no preparations to read")
            return
        owner = (await repo.list_exam_preps_by_ids([candidates[0].id]))[0].user_id

        dashboard = await prepare_dashboard_service.get_dashboard(
            user_id=owner, preparation_limit=6, topic_limit=8, session_limit=6
        )

        print(f"degradedSections   : {dashboard.meta.degraded_sections or 'none'}")
        print(f"activePreparations : {dashboard.summary.active_preparations}")
        print(f"questionsAnswered  : {dashboard.summary.questions_answered}")
        print(f"accuracyPercent    : {dashboard.summary.accuracy_percent!r}")
        print(f"practiceStreak     : {dashboard.summary.practice_streak!r}")
        print(f"milestones         : {len(dashboard.milestones)}")
        print(f"focusTopics        : {len(dashboard.focus_topics)}")
        print(f"recentSessions     : {len(dashboard.recent_sessions)}")

        print("\nwhat the practice entry page will list:")
        for prep in dashboard.preparations:
            print(
                f"  {prep.subject[:34]:<34} practiceReady={prep.practice_ready} "
                f"readiness={prep.average_mastery_percent!r} "
                f"days={prep.days_until_exam!r}"
            )
            action = prep.next_action
            if action:
                print(
                    f"      next: {action.reason_code} -> {action.recommended_mode} "
                    f"x{action.recommended_question_count} ~{action.estimated_minutes}min "
                    f"topic={action.topic_title!r}"
                )
            else:
                print("      next: none (recommendation unavailable)")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
