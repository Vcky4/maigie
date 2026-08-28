"""`list_dashboard_collections`, which was the dashboard's worst N+1.

It ran `3 + 2N` statements: the `Collection` select, its `lazy="selectin"` items load, then a count
query **and** a distinct-types query per collection. Fourteen round trips for six collections, to
print six numbers and six short lists.

That was survivable only because collection auto-seeding had never worked — `find_cross_type_tags`
raised on every call, so `N` was almost always zero. Fixing the seeding query turned a latent N+1
into a live one, which is the argument for grouping it now: the cost of a per-row query is invisible
until the rows arrive.

Now two statements regardless of `N`. Executed against real SQL rather than asserted on mocks,
because the claim is about the query, and a mocked repository would pass against the version that
looped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "collections-user"
OTHER = "collections-intruder"


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
    tables = [
        identity_models.User.__table__,
        pl_models.Collection.__table__,
        pl_models.CollectionItem.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        session.add(identity_models.User(id=OTHER, email="intruder@example.com"))
        await session.commit()

    yield repository_module.personal_learning_repo, factory
    await engine.dispose()


async def _collection(factory, *, id: str, title: str, user_id: str = USER, items=()):
    from src.domains.personal_learning import db_models as pl_models

    async with factory() as session:
        session.add(
            pl_models.Collection(id=id, user_id=user_id, title=title, source_tag=title.lower())
        )
        for index, (entity_type, entity_id) in enumerate(items):
            session.add(
                pl_models.CollectionItem(
                    id=f"{id}-item-{index}",
                    collection_id=id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                )
            )
        await session.commit()


async def test_counts_and_types_are_correct(repo):
    personal_learning_repo, factory = repo
    await _collection(
        factory,
        id="col-1",
        title="Algorithms",
        items=[("note", "n1"), ("note", "n2"), ("saved_resource", "r1")],
    )

    [result] = await personal_learning_repo.list_dashboard_collections(USER)

    assert result["id"] == "col-1"
    assert result["title"] == "Algorithms"
    # Three items across two types. The per-type counts sum to the total by construction, which is
    # what makes grouping by `(collectionId, entityType)` and folding in Python equivalent to two
    # separate aggregate queries.
    assert result["item_count"] == 3
    assert result["entity_types"] == ["note", "saved_resource"]


async def test_an_empty_collection_reports_zero_rather_than_vanishing(repo):
    """A freshly seeded collection has no items yet, and that is a real state.

    Dropping it from the dashboard would make auto-seeding look like it had done nothing.
    """
    personal_learning_repo, factory = repo
    await _collection(factory, id="col-empty", title="Nothing yet", items=[])

    [result] = await personal_learning_repo.list_dashboard_collections(USER)

    assert result["item_count"] == 0
    assert result["entity_types"] == []


async def test_counts_are_not_mixed_between_collections(repo):
    """The failure mode a single grouped query invites: attributing one collection's items to
    another. The per-collection loop could not get this wrong; the grouped version can, so it is
    asserted."""
    personal_learning_repo, factory = repo
    await _collection(factory, id="col-a", title="A", items=[("note", "n1")])
    await _collection(
        factory,
        id="col-b",
        title="B",
        items=[("note", "n2"), ("note", "n3"), ("document", "d1")],
    )

    by_id = {
        row["id"]: row for row in await personal_learning_repo.list_dashboard_collections(USER)
    }

    assert by_id["col-a"]["item_count"] == 1
    assert by_id["col-a"]["entity_types"] == ["note"]
    assert by_id["col-b"]["item_count"] == 3
    assert by_id["col-b"]["entity_types"] == ["document", "note"]


async def test_another_learners_collections_are_not_returned(repo):
    personal_learning_repo, factory = repo
    await _collection(factory, id="mine", title="Mine", items=[("note", "n1")])
    await _collection(factory, id="theirs", title="Theirs", user_id=OTHER, items=[("note", "n2")])

    results = await personal_learning_repo.list_dashboard_collections(USER)

    assert [row["id"] for row in results] == ["mine"]


async def test_soft_deleted_collections_are_excluded(repo):
    from datetime import UTC, datetime

    from src.domains.personal_learning import db_models as pl_models

    personal_learning_repo, factory = repo
    await _collection(factory, id="kept", title="Kept", items=[("note", "n1")])
    await _collection(factory, id="gone", title="Gone", items=[("note", "n2")])
    async with factory() as session:
        collection = await session.get(pl_models.Collection, "gone")
        collection.deleted_at = datetime.now(UTC)
        await session.commit()

    results = await personal_learning_repo.list_dashboard_collections(USER)

    assert [row["id"] for row in results] == ["kept"]


async def test_the_take_limit_is_honoured(repo):
    personal_learning_repo, factory = repo
    for index in range(4):
        await _collection(factory, id=f"col-{index}", title=f"C{index}", items=[("note", "n")])

    results = await personal_learning_repo.list_dashboard_collections(USER, take=2)

    assert len(results) == 2


async def test_the_query_count_does_not_grow_with_the_number_of_collections(repo):
    """The regression guard, and the only assertion here that fails against the old version.

    Two statements for one collection and two for six. The old implementation issued `3 + 2N`, so
    this counted 5 and 15.
    """
    personal_learning_repo, factory = repo
    statements: list[str] = []

    from sqlalchemy import event as sa_event

    engine = factory.kw["bind"]

    @sa_event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    await _collection(factory, id="only", title="Only", items=[("note", "n1")])
    statements.clear()
    await personal_learning_repo.list_dashboard_collections(USER)
    one_collection = len(statements)

    for index in range(5):
        await _collection(factory, id=f"more-{index}", title=f"M{index}", items=[("note", "n")])
    statements.clear()
    await personal_learning_repo.list_dashboard_collections(USER)
    six_collections = len(statements)

    assert one_collection == 2, statements
    assert six_collections == one_collection
