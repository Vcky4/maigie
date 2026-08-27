"""Run one of the adaptive-lifecycle sweeps now, instead of waiting for beat.

The three nightly passes are what put a question in front of a learner, so nothing in either client can
be exercised until one of them has run. There is no way to fake it from the front end: the popups are
driven by rows only these sweeps write — `GoalLifecycleAction` for the goal nudge, and
`ExamPrep.status = AWAITING_REVIEW` for the post-exam review.

    python scripts/db_direct.py python scripts/run_adaptive_sweeps.py goals
    python scripts/db_direct.py python scripts/run_adaptive_sweeps.py preparations
    python scripts/db_direct.py python scripts/run_adaptive_sweeps.py plans

Equivalent to the beat tasks `progress.review_goal_lifecycle` (02:30),
`learning.mark_completed_preparations` (01:00) and `learning.redistribute_drifted_plans` (05:00). Running
by hand does exactly what tonight's run would do — it is not a dry run and it is not idempotent in the
harmless sense: each sweep records that it acted, so a second run inside the cooldown does nothing.

**Read `scripts/check_goal_nudge_state.py` first** if you want to know what a sweep will do before it does
it. In particular, the goal ladder either *extends* a deadline or *asks* whether the goal is still wanted,
and which one depends on whether `GoalProgressSnapshot` holds two or more days for that goal — with fewer
than two there is no rate to extrapolate, so it asks rather than inventing a date. Extending moves a real
deadline; asking does not.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USAGE = "usage: run_adaptive_sweeps.py {goals|preparations|plans}"


async def run(which: str) -> None:
    from src.shared.database.session import ensure_db

    await ensure_db()

    if which == "goals":
        from src.domains.progress.services import goal_lifecycle_service

        counts = await goal_lifecycle_service.review_goals()
        print(f"goal lifecycle actions: {counts or 'none'}")
        print(
            "\nEach `asked_to_confirm` or `warned` sets `pendingNudge` on that goal, which is what makes"
            "\nthe web prompt appear. Reload any page — the prompt is mounted app-wide."
        )
    elif which == "preparations":
        from src.domains.personal_learning.services import exam_prep_service

        moved = await exam_prep_service.mark_preparations_awaiting_review()
        print(f"preparations moved to AWAITING_REVIEW: {moved}")
        print(
            "\nEach one now asks for a review. The web prompt appears app-wide, and opening that"
            "\npreparation shows the same form as a required dialog."
        )
    elif which == "plans":
        from src.domains.personal_learning.services import study_plan_service

        redistributed = await study_plan_service.redistribute_drifted_plans()
        print(f"study plans redistributed: {redistributed}")
        print(
            "\nThis one **moves real dates** on every plan it touches, and tells the learner it did."
            "\nCheck the count in `check_goal_nudge_state.py`'s sibling output before running it widely."
        )
    else:
        raise SystemExit(USAGE)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(USAGE)
    asyncio.run(run(sys.argv[1]))
