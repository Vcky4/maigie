"""`list_collection_items_with_titles` — what a collection row resolves to, and what it drops.

Two claims, both about SQL, so both are executed against a real database rather than asserted on a
mocked repository. In-memory SQLite, following `test_dashboard_collections_query.py`.

1. **A saved resource resolves its `url`.** Added so a client can render the row as an anchor. It
   cannot resolve the address on click instead: putting an `await` between the gesture and the tab is
   what popup blockers stop, so the URL has to arrive with the row. Only saved resources have one —
   notes, decks and documents all open inside the product — and it is nullable even for a resource,
   because `SavedResource.url` is.

2. **A row whose artifact is gone disappears.** There is no foreign key from `CollectionItem.entity_id`
   to the four artifact tables, and the artifacts are hard-deleted, so deleting a note leaves an orphan
   row forever. The read drops it. That is the documented behaviour and the reason the detail response's
   `itemCount` can be lower than the list response's for the same collection — this pins the gap rather
   than closing it, since closing it is a design decision about which number is the real one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "item-resolution-user"


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

    # `Topic`, `Module` and `Course` are pulled in by `Note`'s foreign keys, not used directly.
    import src.domains.knowledge.db_models  # noqa: F401

    # The tables the query needs, plus everything they reference, worked out rather than hand-listed.
    #
    # The query under test LEFT JOINs all four artifact tables, so all four must exist — and `Note`
    # has an FK to `Topic`, which has one to `Module`, and so on. Two approaches were wrong before this
    # one: listing the closure by hand breaks whenever a new FK is added anywhere in the chain, and
    # `create_all` over the whole of `Base.metadata` passes alone but fails in the full suite, because by
    # then another module has registered `ChatMessage` and its Postgres `ARRAY` column has no SQLite
    # compilation. Walking the closure depends on neither.
    seeds = [
        identity_models.User.__table__,
        pl_models.Collection.__table__,
        pl_models.CollectionItem.__table__,
        pl_models.Note.__table__,
        pl_models.FlashcardDeck.__table__,
        pl_models.SavedResource.__table__,
        pl_models.GeneratedDocument.__table__,
    ]
    tables: dict[str, object] = {}
    pending = list(seeds)
    while pending:
        table = pending.pop()
        if table.key in tables:
            continue
        tables[table.key] = table
        pending.extend(key.column.table for key in table.foreign_keys)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=list(tables.values()))

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        await session.commit()

    yield repository_module.personal_learning_repo, factory
    await engine.dispose()


async def _seed(factory, *, items, resources=(), notes=()):
    """One collection holding `items`, alongside whichever artifacts are meant to exist."""
    from src.domains.personal_learning import db_models as pl_models

    async with factory() as session:
        session.add(pl_models.Collection(id="col", user_id=USER, title="Mixed"))
        for resource_id, title, url in resources:
            session.add(
                pl_models.SavedResource(
                    id=resource_id,
                    user_id=USER,
                    title=title,
                    url=url,
                    source_type="web",
                )
            )
        for note_id, title in notes:
            session.add(pl_models.Note(id=note_id, user_id=USER, title=title))
        for index, (entity_type, entity_id) in enumerate(items):
            session.add(
                pl_models.CollectionItem(
                    id=f"item-{index}",
                    collection_id="col",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    position=index,
                )
            )
        await session.commit()


async def test_saved_resource_resolves_its_url(repo):
    personal_learning_repo, factory = repo
    await _seed(
        factory,
        items=[("saved_resource", "res-1")],
        resources=[("res-1", "Dijkstra explained", "https://example.dev/dijkstra")],
    )

    rows = await personal_learning_repo.list_collection_items_with_titles("col")

    assert len(rows) == 1
    assert rows[0]["title"] == "Dijkstra explained"
    assert rows[0]["url"] == "https://example.dev/dijkstra"


async def test_a_resource_saved_without_an_address_resolves_a_null_url(repo):
    """`SavedResource.url` is nullable, so the row is not a link rather than a broken one."""
    personal_learning_repo, factory = repo
    await _seed(
        factory,
        items=[("saved_resource", "res-1")],
        resources=[("res-1", "A lecture with no link", None)],
    )

    rows = await personal_learning_repo.list_collection_items_with_titles("col")

    assert len(rows) == 1, "a resource with no URL is still in the collection"
    assert rows[0]["url"] is None


async def test_a_note_resolves_no_url(repo):
    """Only saved resources carry one. A note opens inside the product."""
    personal_learning_repo, factory = repo
    await _seed(factory, items=[("note", "note-1")], notes=[("note-1", "Graph traversal")])

    rows = await personal_learning_repo.list_collection_items_with_titles("col")

    assert len(rows) == 1
    assert rows[0]["title"] == "Graph traversal"
    assert rows[0]["url"] is None


async def test_url_is_not_crossed_between_types(repo):
    """A note and a resource sharing an id must not borrow each other's columns.

    The join predicates include `entity_type`, which is what keeps them apart. Without that, the
    resource alias would match the note row on id alone and hand a note someone else's URL.
    """
    personal_learning_repo, factory = repo
    await _seed(
        factory,
        items=[("note", "shared-id"), ("saved_resource", "shared-id")],
        notes=[("shared-id", "A note")],
        resources=[("shared-id", "A resource", "https://example.dev/resource")],
    )

    rows = await personal_learning_repo.list_collection_items_with_titles("col")
    by_type = {row["entity_type"]: row for row in rows}

    assert by_type["note"]["title"] == "A note"
    assert by_type["note"]["url"] is None
    assert by_type["saved_resource"]["title"] == "A resource"
    assert by_type["saved_resource"]["url"] == "https://example.dev/resource"


async def test_an_item_whose_artifact_is_gone_is_dropped(repo):
    """The documented silent-disappearance behaviour, and the source of the `itemCount` divergence."""
    personal_learning_repo, factory = repo
    await _seed(
        factory,
        # `orphan` has no matching artifact — nothing enforces one, since `entity_id` has no FK.
        items=[("saved_resource", "res-1"), ("saved_resource", "orphan")],
        resources=[("res-1", "Still here", "https://example.dev/here")],
    )

    rows = await personal_learning_repo.list_collection_items_with_titles("col")

    assert len(rows) == 1, "the orphan row is filtered out of the read"
    assert rows[0]["entity_id"] == "res-1"

    # And the divergence that follows from it: the membership rows the list endpoint counts still
    # include the orphan, so the two endpoints report different counts for one collection.
    async with factory() as session:
        collection = await personal_learning_repo.find_collection("col", USER, session=session)
        assert len(collection.items) == 2
