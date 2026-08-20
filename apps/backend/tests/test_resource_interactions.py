"""Resource interaction counters, which had never incremented.

`record_interaction` built `{"clickCount": {"increment": 1}}` — Prisma's dialect — and handed
it to `update_resource`, which passes its dict into `values(**data)`. Binding a dict to an
integer column raises, so every click and every bookmark since the feature shipped ended in
an exception and both counters sat at zero. Two things made it invisible: the web client
swallows the failure deliberately ("analytics should not block opening"), and a counter that
reads 0 looks like a resource nobody has opened.

SQLite in memory with foreign keys enforced, matching the course and flashcard repository
suites. An increment is SQL, and asserting on the Python that composes it would have passed
against the broken version too — the dict was well-formed Python.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "resource-test-user"
OTHER_USER = "resource-test-intruder"


@pytest.fixture
async def repo(monkeypatch):
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.knowledge import db_models as knowledge_models
    from src.domains.knowledge import repository as repository_module
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
        knowledge_models.Resource.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        session.add(identity_models.User(id=OTHER_USER, email="intruder@example.com"))
        await session.commit()

    yield repository_module.knowledge_repo
    await engine.dispose()


async def _resource(repo, *, user_id=USER, title="Dijkstra explained", **extra):
    return await repo.create_resource(
        {"userId": user_id, "title": title, "url": "https://example.com/x", **extra}
    )


# ---------------------------------------------------------------------------
# The counters
# ---------------------------------------------------------------------------


async def test_a_new_resource_starts_at_zero(repo):
    resource = await _resource(repo)
    assert resource.click_count == 0
    assert resource.bookmark_count == 0
    assert resource.last_accessed_at is None


async def test_click_increments_and_stamps_last_accessed(repo):
    resource = await _resource(repo)

    await repo.increment_resource_counter(
        resource.id, column="clickCount", touch_last_accessed=True
    )

    refreshed = await repo.find_resource(resource.id, USER)
    assert refreshed.click_count == 1
    # The column exists to order "recently used"; an increment that left it null would
    # keep the ordering unanswerable.
    assert refreshed.last_accessed_at is not None


async def test_clicks_accumulate(repo):
    """The regression that matters. The broken version raised on the first call, so any
    assertion of "greater than zero" would have failed loudly — but a single increment
    could also be faked by an overwrite, and this cannot."""
    resource = await _resource(repo)

    for _ in range(3):
        await repo.increment_resource_counter(resource.id, column="clickCount")

    refreshed = await repo.find_resource(resource.id, USER)
    assert refreshed.click_count == 3


async def test_bookmark_increments_without_touching_last_accessed(repo):
    """Bookmarking is not opening. Stamping `lastAccessedAt` here would report the
    resource as recently read on the strength of it being filed."""
    resource = await _resource(repo)

    await repo.increment_resource_counter(resource.id, column="bookmarkCount")

    refreshed = await repo.find_resource(resource.id, USER)
    assert refreshed.bookmark_count == 1
    assert refreshed.click_count == 0
    assert refreshed.last_accessed_at is None


async def test_the_two_counters_are_independent(repo):
    resource = await _resource(repo)

    await repo.increment_resource_counter(resource.id, column="clickCount")
    await repo.increment_resource_counter(resource.id, column="clickCount")
    await repo.increment_resource_counter(resource.id, column="bookmarkCount")

    refreshed = await repo.find_resource(resource.id, USER)
    assert (refreshed.click_count, refreshed.bookmark_count) == (2, 1)


async def test_one_resource_is_incremented_not_all_of_them(repo):
    """The `where` clause, which a statement-level update makes easy to get wrong."""
    mine = await _resource(repo, title="Mine")
    other = await _resource(repo, title="Also mine")

    await repo.increment_resource_counter(mine.id, column="clickCount")

    assert (await repo.find_resource(mine.id, USER)).click_count == 1
    assert (await repo.find_resource(other.id, USER)).click_count == 0


async def test_an_unknown_counter_is_refused(repo):
    """The column name is not interpolated into SQL from the caller's string.

    `record_interaction` maps a closed set of interaction kinds onto these two names, so a
    third name means the mapping and this method have drifted — worth an error rather than
    a silent no-op.
    """
    resource = await _resource(repo)

    with pytest.raises(ValueError, match="Not a resource counter"):
        await repo.increment_resource_counter(resource.id, column="viewCount")


# ---------------------------------------------------------------------------
# Through the service, which is what the route calls
# ---------------------------------------------------------------------------


async def test_service_records_a_click(repo):
    from src.domains.knowledge.services import resource_service

    resource = await _resource(repo)

    await resource_service.record_interaction(
        user_id=USER, resource_id=resource.id, interaction_type="RESOURCE_CLICK"
    )

    refreshed = await repo.find_resource(resource.id, USER)
    assert refreshed.click_count == 1
    assert refreshed.last_accessed_at is not None


async def test_service_records_a_bookmark(repo):
    from src.domains.knowledge.services import resource_service

    resource = await _resource(repo)

    await resource_service.record_interaction(
        user_id=USER, resource_id=resource.id, interaction_type="RESOURCE_BOOKMARK"
    )

    assert (await repo.find_resource(resource.id, USER)).bookmark_count == 1


async def test_service_refuses_someone_elses_resource(repo):
    """Ownership is checked before the write, so a counter cannot be moved on a resource
    the caller cannot see."""
    from src.domains.knowledge.services import resource_service
    from src.shared.exceptions import NotFoundError

    theirs = await _resource(repo, user_id=OTHER_USER, title="Theirs")

    with pytest.raises(NotFoundError):
        await resource_service.record_interaction(
            user_id=USER, resource_id=theirs.id, interaction_type="RESOURCE_CLICK"
        )

    assert (await repo.find_resource(theirs.id, OTHER_USER)).click_count == 0
