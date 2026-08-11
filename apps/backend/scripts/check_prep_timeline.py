"""What would a preparation's timeline actually show?

The timeline tab is about to ship, reading `GET /preparations/{id}/timeline`. That
endpoint merges items from **every** study plan ever generated for the preparation,
with no status filter. So this checks, before the tab exists:

- how many preparations have a plan at all (the tab's empty state depends on it),
- how many have more than one, because those learners would see every topic listed
  twice on overlapping days,
- whether any preparation's target date has already passed, because plan generation
  divides by the days remaining and crams everything into one day when there are none.

Read-only.

    poetry run python scripts/check_prep_timeline.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from sqlalchemy import func, select

    from src.domains.personal_learning.db_models import (
        ExamPrep,
        PrepTopic,
        StudyPlan,
        StudyPlanItem,
    )
    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            preps = (
                await session.execute(
                    select(ExamPrep.id, ExamPrep.subject, ExamPrep.status, ExamPrep.exam_date)
                )
            ).all()

            plans = (
                await session.execute(
                    select(StudyPlan.id, StudyPlan.prep_id, StudyPlan.status, StudyPlan.created_at)
                )
            ).all()

            item_counts = dict(
                (
                    await session.execute(
                        select(StudyPlanItem.plan_id, func.count(StudyPlanItem.id)).group_by(
                            StudyPlanItem.plan_id
                        )
                    )
                ).all()
            )

            topic_counts = dict(
                (
                    await session.execute(
                        select(PrepTopic.prep_id, func.count(PrepTopic.id)).group_by(
                            PrepTopic.prep_id
                        )
                    )
                ).all()
            )

        now = datetime.now(UTC)
        plans_by_prep: dict[str, list] = {}
        for plan in plans:
            if plan.prep_id:
                plans_by_prep.setdefault(plan.prep_id, []).append(plan)

        print(f"preparations                    : {len(preps)}")
        print(f"study plans (any owner)         : {len(plans)}")
        print(f"  linked to a preparation       : {sum(len(v) for v in plans_by_prep.values())}")
        print(f"  orphaned / goal-only          : {len([p for p in plans if not p.prep_id])}")
        print()

        with_plan = 0
        with_multiple = 0
        date_passed = 0
        no_topics = 0
        statuses: Counter[str] = Counter()

        print(f"{'preparation':34} {'plans':>5} {'items':>5} {'topics':>6}  target")
        print("-" * 78)
        for prep in preps:
            prep_plans = plans_by_prep.get(prep.id, [])
            items = sum(item_counts.get(plan.id, 0) for plan in prep_plans)
            topics = topic_counts.get(prep.id, 0)
            for plan in prep_plans:
                statuses[plan.status or "?"] += 1
            if prep_plans:
                with_plan += 1
            if len(prep_plans) > 1:
                with_multiple += 1
            if topics == 0:
                no_topics += 1

            target = prep.exam_date
            if target is not None and target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            passed = target is not None and target < now
            if passed:
                date_passed += 1

            label = (prep.subject or "")[:32]
            when = target.date().isoformat() if target else "none"
            print(
                f"{label:34} {len(prep_plans):>5} {items:>5} {topics:>6}  "
                f"{when}{'  PASSED' if passed else ''}"
            )

        print()
        print(f"preparations with a plan        : {with_plan} / {len(preps)}")
        print(f"  with more than one plan       : {with_multiple}")
        print(f"plan statuses                   : {dict(statuses) or '{}'}")
        print(f"preparations with no topics     : {no_topics} / {len(preps)}")
        print(f"target date already passed      : {date_passed} / {len(preps)}")
        print()

        if with_plan == 0:
            print(
                "FINDING: no preparation has a study plan, so the timeline tab renders its\n"
                "empty state for every learner and `hasStudyPlan: false` is the only path\n"
                "that will be exercised on ship. Generation is the feature; the timeline is\n"
                "what it produces."
            )
        if with_multiple:
            print(
                f"FINDING: {with_multiple} preparation(s) have more than one plan. The timeline\n"
                "merges items from all of them with no status filter, so each topic appears\n"
                "once per plan on overlapping days — a duplicated to-do list, not history."
            )
        if no_topics:
            print(
                f"FINDING: {no_topics} preparation(s) have no topics. Generating a plan for one\n"
                "falls through to `_generate_topics_from_goal`, which asks an LLM to invent\n"
                "items from the title — unrelated to anything the learner uploaded."
            )
        if date_passed:
            print(
                f"FINDING: {date_passed} preparation(s) have a target date in the past.\n"
                "`days_available = max(1, (deadline - now).days)` makes that one day, so every\n"
                "topic is scheduled today regardless of how much work it is."
            )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
