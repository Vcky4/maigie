"""Atomic goal schedule replacement and its post-commit Calendar effects."""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from src.shared.exceptions import NotFoundError, ValidationError  # noqa: E402

USER = "schedule-replacement-user"
OTHER_USER = "schedule-replacement-intruder"
GOAL = "schedule-replacement-goal"
NOW = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
async def repo(monkeypatch):
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.knowledge import db_models as knowledge_models  # noqa: F401
    from src.domains.learning_spaces import db_models as space_models  # noqa: F401
    from src.domains.personal_learning import db_models as prep_models  # noqa: F401
    from src.domains.progress import db_models as progress_models
    from src.domains.progress import repository as repository_module
    from src.shared.database.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    tables = [
        identity_models.User.__table__,
        knowledge_models.Course.__table__,
        knowledge_models.Module.__table__,
        knowledge_models.Topic.__table__,
        space_models.Space.__table__,
        prep_models.ExamPrep.__table__,
        progress_models.Goal.__table__,
        progress_models.GoalMilestone.__table__,
        progress_models.ReviewItem.__table__,
        progress_models.ScheduleBlock.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="schedule@example.com"))
        session.add(identity_models.User(id=OTHER_USER, email="intruder@example.com"))
        await session.commit()

    goal = await repository_module.progress_repo.create_goal(
        {"userId": USER, "title": "Pass algorithms", "targetDate": NOW + timedelta(days=30)}
    )
    # Keep a stable identifier in assertions without depending on generated-ID implementation details.
    global GOAL
    GOAL = goal.id

    yield repository_module.progress_repo
    await engine.dispose()


def _block(title: str, *, start_offset: int = 0, end_at: datetime | None = None):
    start_at = NOW + timedelta(days=start_offset)
    return {
        "title": title,
        "startAt": start_at,
        "endAt": end_at if end_at is not None else start_at + timedelta(hours=1),
    }


async def _old_block(repo):
    return await repo.create_block(
        {
            "userId": USER,
            "goalId": GOAL,
            "googleCalendarEventId": "old-calendar-event",
            **_block("Old session"),
        }
    )


@pytest.mark.asyncio
async def test_repository_replaces_the_complete_owned_set(repo):
    old = await _old_block(repo)

    new_rows, replaced_count, event_ids = await repo.replace_blocks_for_goal(
        goal_id=GOAL,
        user_id=USER,
        blocks=[_block("First", start_offset=1), _block("Second", start_offset=2)],
        goal_data={"targetDate": NOW + timedelta(days=60), "description": "regenerated"},
    )

    stored, total = await repo.list_blocks(USER, where={"goalId": GOAL})
    goal = await repo.find_goal(GOAL, USER)
    assert replaced_count == 1
    assert event_ids == ["old-calendar-event"]
    assert {row.title for row in new_rows} == {"First", "Second"}
    assert {row.title for row in stored} == {"First", "Second"}
    assert old.id not in {row.id for row in stored}
    assert total == 2
    assert all(row.user_id == USER and row.goal_id == GOAL for row in stored)
    assert goal is not None
    assert goal.target_date.date() == (NOW + timedelta(days=60)).date()
    assert goal.description == "regenerated"


@pytest.mark.asyncio
async def test_repository_insert_failure_preserves_the_old_set(repo):
    old = await _old_block(repo)
    invalid = _block("Invalid", start_offset=2)
    invalid["endAt"] = None

    with pytest.raises(IntegrityError):
        await repo.replace_blocks_for_goal(
            goal_id=GOAL,
            user_id=USER,
            blocks=[_block("Would have been inserted", start_offset=1), invalid],
            goal_data={"targetDate": NOW + timedelta(days=90), "description": "new plan"},
        )

    stored, total = await repo.list_blocks(USER, where={"goalId": GOAL})
    goal = await repo.find_goal(GOAL, USER)
    assert total == 1
    assert stored[0].id == old.id
    assert stored[0].title == "Old session"
    assert goal is not None
    assert goal.target_date.date() == (NOW + timedelta(days=30)).date()
    assert goal.description is None


@pytest.mark.asyncio
async def test_repository_refuses_an_unowned_goal_without_touching_it(repo):
    old = await _old_block(repo)

    with pytest.raises(NotFoundError):
        await repo.replace_blocks_for_goal(
            goal_id=GOAL,
            user_id=OTHER_USER,
            blocks=[_block("Intruding replacement")],
        )

    stored, total = await repo.list_blocks(USER, where={"goalId": GOAL})
    assert total == 1
    assert stored[0].id == old.id


@pytest.mark.asyncio
async def test_repository_refuses_an_empty_replacement(repo):
    old = await _old_block(repo)

    with pytest.raises(ValidationError):
        await repo.replace_blocks_for_goal(goal_id=GOAL, user_id=USER, blocks=[])

    stored, total = await repo.list_blocks(USER, where={"goalId": GOAL})
    assert total == 1
    assert stored[0].id == old.id


@pytest.mark.asyncio
async def test_service_performs_calendar_effects_after_repository_success():
    from src.domains.progress.services import schedule_service
    from src.integrations import google_calendar

    rows = [SimpleNamespace(id="new-1"), SimpleNamespace(id="new-2")]
    replace = AsyncMock(return_value=(rows, 2, ["event-1", "event-2"]))
    delete_event = AsyncMock()
    sync = AsyncMock()

    with (
        patch.object(schedule_service.progress_repo, "replace_blocks_for_goal", replace),
        patch.object(google_calendar, "delete_schedule_block_event", delete_event),
        patch.object(google_calendar, "sync_schedule_block", sync),
    ):
        result = await schedule_service.replace_blocks_for_goal(
            user_id=USER, goal_id=GOAL, blocks=[_block("New")]
        )

    assert result == (rows, 2)
    assert delete_event.await_args_list[0].args == (USER, "event-1")
    assert delete_event.await_args_list[1].args == (USER, "event-2")
    assert sync.await_args_list[0].args == (USER, "new-1")
    assert sync.await_args_list[1].args == (USER, "new-2")


@pytest.mark.asyncio
async def test_service_has_no_calendar_effects_when_repository_fails():
    from src.domains.progress.services import schedule_service
    from src.integrations import google_calendar

    replace = AsyncMock(side_effect=IntegrityError("insert", {}, RuntimeError("failed")))
    delete_event = AsyncMock()
    sync = AsyncMock()

    with (
        patch.object(schedule_service.progress_repo, "replace_blocks_for_goal", replace),
        patch.object(google_calendar, "delete_schedule_block_event", delete_event),
        patch.object(google_calendar, "sync_schedule_block", sync),
        pytest.raises(IntegrityError),
    ):
        await schedule_service.replace_blocks_for_goal(
            user_id=USER, goal_id=GOAL, blocks=[_block("New")]
        )

    delete_event.assert_not_awaited()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_errors_do_not_fail_a_committed_replacement():
    from src.domains.progress.services import schedule_service
    from src.integrations import google_calendar

    rows = [SimpleNamespace(id="new-1")]
    replace = AsyncMock(return_value=(rows, 1, ["event-1"]))
    delete_event = AsyncMock(side_effect=RuntimeError("calendar unavailable"))
    sync = AsyncMock(side_effect=RuntimeError("calendar unavailable"))

    with (
        patch.object(schedule_service.progress_repo, "replace_blocks_for_goal", replace),
        patch.object(google_calendar, "delete_schedule_block_event", delete_event),
        patch.object(google_calendar, "sync_schedule_block", sync),
    ):
        result = await schedule_service.replace_blocks_for_goal(
            user_id=USER, goal_id=GOAL, blocks=[_block("New")]
        )

    assert result == (rows, 1)
    delete_event.assert_awaited_once_with(USER, "event-1")
    sync.assert_awaited_once_with(USER, "new-1")


@pytest.mark.asyncio
async def test_unconfirmed_calendar_deletion_is_reported_but_does_not_fail(caplog):
    from src.domains.progress.services import schedule_service
    from src.integrations import google_calendar

    rows = [SimpleNamespace(id="new-1")]
    replace = AsyncMock(return_value=(rows, 1, ["event-1"]))
    delete_event = AsyncMock(return_value=False)
    sync = AsyncMock()

    with (
        patch.object(schedule_service.progress_repo, "replace_blocks_for_goal", replace),
        patch.object(google_calendar, "delete_schedule_block_event", delete_event),
        patch.object(google_calendar, "sync_schedule_block", sync),
        caplog.at_level("WARNING"),
    ):
        result = await schedule_service.replace_blocks_for_goal(
            user_id=USER, goal_id=GOAL, blocks=[_block("New")]
        )

    assert result == (rows, 1)
    assert "Calendar event removal was not confirmed" in caplog.text
    sync.assert_awaited_once_with(USER, "new-1")
