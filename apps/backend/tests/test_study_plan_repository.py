"""Study plan queries, exercised against a real database engine.

SQLite in memory with foreign keys enforced, for the same reason as the flashcard
repository tests: these are grouped aggregates, scoped updates, a cascade and a
cross-table join, and none of them is checked by asserting on the Python around them.
Running here rather than skipping whenever no Postgres is configured means the two
defects below stay closed.

Route-level ownership is covered in ``test_study_plan_api.py``, which needs Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "plan-test-user"
OTHER_USER = "plan-test-intruder"


@pytest.fixture
async def repo(monkeypatch):
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.personal_learning import db_models as pl_models
    from src.domains.personal_learning import repository as repository_module
    from src.shared.database.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Only the tables under test plus their foreign-key parents. The shared metadata is
    # one namespace for every domain and some of it uses Postgres-only column types.
    tables = [
        identity_models.User.__table__,
        pl_models.ExamPrep.__table__,
        pl_models.StudyPlan.__table__,
        pl_models.StudyPlanItem.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        session.add(identity_models.User(id=OTHER_USER, email="intruder@example.com"))
        await session.commit()

    yield repository_module.personal_learning_repo
    await engine.dispose()


async def _plan(repo, *, user_id=USER, title="Interview prep", days=14, status="ACTIVE", **extra):
    return await repo.create_study_plan(
        {
            "userId": user_id,
            "title": title,
            "goalDescription": "Solve medium problems confidently",
            "deadline": datetime.now(UTC) + timedelta(days=days),
            "status": status,
            "totalItems": 0,
            "completedItems": 0,
            **extra,
        }
    )


async def _item(repo, plan, *, title, day_offset=0, phase=None, minutes=30, status="PENDING"):
    return await repo.create_plan_item(
        {
            "planId": plan.id,
            "title": title,
            "scheduledDate": datetime.now(UTC) + timedelta(days=day_offset),
            "estimatedMinutes": minutes,
            "itemType": "STUDY",
            "phase": phase,
            "status": status,
        }
    )


@pytest.fixture
async def plan_with_items(repo):
    plan = await _plan(repo)
    items = [
        await _item(repo, plan, title="Big-O", day_offset=0, phase="Foundations"),
        await _item(repo, plan, title="Arrays", day_offset=1, phase="Foundations"),
        await _item(repo, plan, title="Graphs", day_offset=2, phase="Core patterns"),
        await _item(repo, plan, title="Mock round", day_offset=3, phase="Interview practice"),
    ]
    await repo.recount_plan_progress(plan.id)
    return plan, items


# ---------------------------------------------------------------------------
# The two defects
# ---------------------------------------------------------------------------


class TestItemUpdateIsScopedToItsPlan:
    async def test_refuses_an_item_from_another_plan(self, repo, plan_with_items):
        """The cross-user write this scoping exists to stop.

        `update_plan_item` matched on the item id alone, and the only caller checked
        that the *plan* belonged to the learner without checking that the *item*
        belonged to the plan. So `POST /study-plans/{myPlan}/items/{theirItem}/complete`
        wrote status and completedAt onto a row the caller did not own.
        """
        _, items = plan_with_items
        intruder_plan = await _plan(repo, user_id=OTHER_USER, title="Theirs")
        victim_item = await _item(repo, intruder_plan, title="Their task")

        result = await repo.update_plan_item(
            victim_item.id, {"status": "COMPLETED"}, plan_id=items[0].plan_id
        )
        assert result is None

        unchanged = await repo.list_plan_items(intruder_plan.id)
        assert [item.status for item in unchanged] == ["PENDING"]

    async def test_updates_an_item_in_its_own_plan(self, repo, plan_with_items):
        plan, items = plan_with_items
        updated = await repo.update_plan_item(
            items[0].id, {"status": "COMPLETED"}, plan_id=plan.id
        )
        assert updated is not None
        assert updated.status == "COMPLETED"

    async def test_delete_is_scoped_the_same_way(self, repo, plan_with_items):
        plan, _ = plan_with_items
        intruder_plan = await _plan(repo, user_id=OTHER_USER, title="Theirs")
        victim_item = await _item(repo, intruder_plan, title="Their task")

        assert await repo.delete_plan_item(victim_item.id, plan_id=plan.id) is False
        assert len(await repo.list_plan_items(intruder_plan.id)) == 1


class TestProgressIsDerived:
    async def test_counts_come_from_the_items(self, repo, plan_with_items):
        plan, items = plan_with_items
        await repo.update_plan_item(items[0].id, {"status": "COMPLETED"}, plan_id=plan.id)
        completed, total = await repo.recount_plan_progress(plan.id)
        assert (completed, total) == (1, 4)

    async def test_completing_the_same_item_twice_counts_once(self, repo, plan_with_items):
        """The double-count defect.

        `completedItems` was incremented on every completion without regard for the
        item's existing status, so a repeated completion counted twice and progress
        could pass 100%. A derived count cannot do that.
        """
        plan, items = plan_with_items
        for _ in range(3):
            await repo.update_plan_item(items[0].id, {"status": "COMPLETED"}, plan_id=plan.id)
            await repo.recount_plan_progress(plan.id)

        completed, total = await repo.recount_plan_progress(plan.id)
        assert (completed, total) == (1, 4)
        assert completed <= total

    async def test_uncompleting_lowers_the_count(self, repo, plan_with_items):
        plan, items = plan_with_items
        await repo.update_plan_item(items[0].id, {"status": "COMPLETED"}, plan_id=plan.id)
        await repo.recount_plan_progress(plan.id)
        await repo.update_plan_item(items[0].id, {"status": "PENDING"}, plan_id=plan.id)
        completed, _ = await repo.recount_plan_progress(plan.id)
        assert completed == 0

    async def test_skipped_items_do_not_count_as_completed(self, repo, plan_with_items):
        """Skipping is "not doing this", which is not progress."""
        plan, items = plan_with_items
        await repo.update_plan_item(items[0].id, {"status": "SKIPPED"}, plan_id=plan.id)
        completed, total = await repo.recount_plan_progress(plan.id)
        assert (completed, total) == (0, 4)

    async def test_stored_counts_are_written_not_just_returned(self, repo, plan_with_items):
        plan, items = plan_with_items
        await repo.update_plan_item(items[0].id, {"status": "COMPLETED"}, plan_id=plan.id)
        await repo.recount_plan_progress(plan.id)
        stored = await repo.get_study_plan(plan.id, USER)
        assert (stored.completed_items, stored.total_items) == (1, 4)

    async def test_removing_an_item_shrinks_the_total(self, repo, plan_with_items):
        plan, items = plan_with_items
        await repo.delete_plan_item(items[3].id, plan_id=plan.id)
        completed, total = await repo.recount_plan_progress(plan.id)
        assert (completed, total) == (0, 3)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestPaginatedListing:
    async def test_returns_every_status_by_default(self, repo):
        """The previous listing hard-filtered ACTIVE, so a Completed tab matched nothing."""
        await _plan(repo, title="Active one", status="ACTIVE")
        await _plan(repo, title="Paused one", status="PAUSED")
        await _plan(repo, title="Done one", status="COMPLETED")

        plans, total = await repo.list_plans_paginated(USER)
        assert total == 3
        assert {plan.status for plan in plans} == {"ACTIVE", "PAUSED", "COMPLETED"}

    async def test_filters_by_status(self, repo):
        await _plan(repo, title="Active one", status="ACTIVE")
        await _plan(repo, title="Paused one", status="PAUSED")

        plans, total = await repo.list_plans_paginated(USER, status="PAUSED")
        assert total == 1
        assert plans[0].title == "Paused one"

    async def test_searches_title_and_goal(self, repo):
        await _plan(repo, title="German vocabulary")
        plans, total = await repo.list_plans_paginated(USER, search="german")
        assert total == 1
        by_goal, total_by_goal = await repo.list_plans_paginated(USER, search="medium problems")
        assert total_by_goal >= 1
        assert plans and by_goal

    async def test_orders_by_deadline_and_pages_without_overlap(self, repo):
        # Same deadline on every plan, so only the id tie-break separates them.
        deadline = datetime.now(UTC) + timedelta(days=30)
        for index in range(6):
            await repo.create_study_plan(
                {
                    "userId": USER,
                    "title": f"Plan {index}",
                    "deadline": deadline,
                    "status": "ACTIVE",
                    "totalItems": 0,
                    "completedItems": 0,
                }
            )

        collected: list[str] = []
        for page in range(3):
            plans, total = await repo.list_plans_paginated(USER, skip=page * 2, take=2)
            collected.extend(plan.id for plan in plans)
        assert total == 6
        assert len(set(collected)) == 6, "paging lost or repeated a plan"

    async def test_scoped_to_the_caller(self, repo):
        await _plan(repo, user_id=OTHER_USER, title="Theirs")
        _, total = await repo.list_plans_paginated(USER)
        assert total == 0

    async def test_plan_columns_round_trip(self, repo):
        plan = await _plan(
            repo,
            weeklyGoalMinutes=240,
            skills=["Graph traversal", "Complexity analysis"],
            strategy="ADAPTIVE",
        )
        stored = await repo.get_study_plan(plan.id, USER)
        assert stored.weekly_goal_minutes == 240
        assert stored.skills == ["Graph traversal", "Complexity analysis"]
        assert stored.strategy == "ADAPTIVE"

    async def test_items_carry_their_phase(self, repo, plan_with_items):
        plan, _ = plan_with_items
        stored = await repo.get_study_plan(plan.id, USER)
        assert [item.phase for item in stored.items] == [
            "Foundations",
            "Foundations",
            "Core patterns",
            "Interview practice",
        ]


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------


class TestItemsDueBy:
    async def test_includes_overdue_and_today_but_not_later(self, repo):
        plan = await _plan(repo)
        await _item(repo, plan, title="Slipped", day_offset=-3)
        await _item(repo, plan, title="Today", day_offset=0)
        await _item(repo, plan, title="Next week", day_offset=7)

        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert [item.title for item, _ in rows] == ["Slipped", "Today"]

    async def test_returns_the_plan_alongside_each_item(self, repo):
        """A cross-plan list is unreadable without saying which plan each row came from."""
        plan = await _plan(repo, title="Interview prep")
        await _item(repo, plan, title="Today", day_offset=0)
        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert rows[0][1].title == "Interview prep"

    async def test_excludes_paused_plans(self, repo):
        """Pausing is the learner saying they are not working on this now."""
        paused = await _plan(repo, title="Paused", status="PAUSED")
        await _item(repo, paused, title="Should not appear", day_offset=0)
        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert rows == []

    async def test_excludes_completed_and_skipped_items(self, repo):
        plan = await _plan(repo)
        await _item(repo, plan, title="Done", day_offset=0, status="COMPLETED")
        await _item(repo, plan, title="Skipped", day_offset=0, status="SKIPPED")
        await _item(repo, plan, title="Pending", day_offset=0)
        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert [item.title for item, _ in rows] == ["Pending"]

    async def test_spans_several_plans(self, repo):
        first = await _plan(repo, title="First", days=10)
        second = await _plan(repo, title="Second", days=20)
        await _item(repo, first, title="A", day_offset=0)
        await _item(repo, second, title="B", day_offset=0)
        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert {plan.title for _, plan in rows} == {"First", "Second"}

    async def test_scoped_to_the_caller(self, repo):
        plan = await _plan(repo, user_id=OTHER_USER)
        await _item(repo, plan, title="Theirs", day_offset=0)
        rows = await repo.list_items_due_by(USER, until=datetime.now(UTC) + timedelta(hours=6))
        assert rows == []


# ---------------------------------------------------------------------------
# Plan update and delete
# ---------------------------------------------------------------------------


class TestPlanMetrics:
    async def test_counts_planned_minutes_only_for_completed_work(self, repo):
        """Planned effort on work that got done — not measured time at a desk."""
        plan = await _plan(repo)
        await _item(repo, plan, title="Done", minutes=45, status="COMPLETED")
        await _item(repo, plan, title="Pending", minutes=30)

        metrics = await repo.get_plan_metrics(plan.id)
        assert metrics["completed_minutes"] == 45
        assert metrics["planned_minutes"] == 75

    async def test_counts_practice_and_review_separately_from_study(self, repo):
        plan = await _plan(repo)
        for title, item_type in (
            ("Read", "STUDY"),
            ("Recap", "REVIEW"),
            ("Drill", "PRACTICE"),
        ):
            await repo.create_plan_item(
                {
                    "planId": plan.id,
                    "title": title,
                    "scheduledDate": datetime.now(UTC),
                    "estimatedMinutes": 20,
                    "itemType": item_type,
                    "status": "COMPLETED",
                }
            )
        metrics = await repo.get_plan_metrics(plan.id)
        assert metrics["practice_completed"] == 2

    async def test_reports_skipped_work(self, repo):
        """A plan where a third was skipped reads differently from one all done."""
        plan = await _plan(repo)
        await _item(repo, plan, title="Skipped", status="SKIPPED")
        await _item(repo, plan, title="Done", status="COMPLETED")
        metrics = await repo.get_plan_metrics(plan.id)
        assert metrics["skipped_items"] == 1

    async def test_active_dates_come_from_completion_timestamps(self, repo):
        plan = await _plan(repo)
        now = datetime.now(UTC)
        for offset in (0, 0, 1, 3):
            item = await _item(repo, plan, title=f"Task {offset}")
            await repo.update_plan_item(
                item.id,
                {"status": "COMPLETED", "completedAt": now - timedelta(days=offset)},
                plan_id=plan.id,
            )
        metrics = await repo.get_plan_metrics(plan.id)
        # Two items completed today collapse to one active day.
        assert len(metrics["active_dates"]) == 3

    async def test_an_untouched_plan_has_no_active_days(self, repo, plan_with_items):
        plan, _ = plan_with_items
        metrics = await repo.get_plan_metrics(plan.id)
        assert metrics["active_dates"] == []
        assert metrics["completed_minutes"] == 0


class TestPlanWrites:
    async def test_updates_a_plan_the_caller_owns(self, repo, plan_with_items):
        plan, _ = plan_with_items
        updated = await repo.update_study_plan(plan.id, USER, {"status": "PAUSED"})
        assert updated is not None
        assert updated.status == "PAUSED"

    async def test_refuses_another_learners_plan(self, repo, plan_with_items):
        plan, _ = plan_with_items
        assert await repo.update_study_plan(plan.id, OTHER_USER, {"title": "Theirs"}) is None
        unchanged = await repo.get_study_plan(plan.id, USER)
        assert unchanged.title == "Interview prep"

    async def test_delete_removes_the_plan_and_its_items(self, repo, plan_with_items):
        """Cascades, unlike deck deletion: an item is a slot, not authored content."""
        plan, items = plan_with_items
        assert await repo.delete_study_plan(plan.id, USER) is True
        assert await repo.get_study_plan(plan.id, USER) is None
        assert await repo.list_plan_items(plan.id) == []
        assert items

    async def test_delete_refuses_another_learners_plan(self, repo, plan_with_items):
        plan, _ = plan_with_items
        assert await repo.delete_study_plan(plan.id, OTHER_USER) is False
        assert await repo.get_study_plan(plan.id, USER) is not None
