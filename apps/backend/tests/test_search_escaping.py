"""Search terms containing `LIKE` wildcards, across every search in the backend.

`%` and `_` are `LIKE` syntax. An unescaped term containing either **widens** the search instead of
narrowing it, so `100%` matched every row and `a_b` matched `axb`. The query succeeds, nothing is
logged, and the result is a plausible page of the wrong rows — which is why eleven of the twelve
`ilike` call sites shipped this way and none was ever reported.

The one that was correct had a private `_escape_like` on `KnowledgeRepository`, reachable only from
course search. That is the actual lesson: a search-safety helper scoped to one of twelve searches is
not a helper. It now lives in `src.shared.database.search` as `ilike_any`, which escapes the term,
passes the escape character and combines the columns in one call, so the three things a call site has
to get right cannot be got wrong one at a time.

`escape="\\"` is passed explicitly rather than relying on a default: Postgres defaults to backslash
but SQLite does not, and these tests run on SQLite — so an implementation that omitted it would pass
here and fail in production, which is the wrong way round.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.shared.database.search import contains_pattern, escape_like, ilike_any

USER = "search-escape-user"


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term,expected",
    [
        ("plain", "plain"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("%_%", "\\%\\_\\%"),
        # The backslash has to be escaped first, or it double-escapes what follows.
        ("back\\slash", "back\\\\slash"),
        ("100%\\_", "100\\%\\\\\\_"),
        ("", ""),
    ],
)
def test_escape_like(term, expected):
    assert escape_like(term) == expected


def test_contains_pattern_wraps_the_escaped_term():
    assert contains_pattern("100%") == "%100\\%%"


def test_ilike_any_needs_a_column():
    with pytest.raises(ValueError):
        ilike_any("term")


def test_ilike_any_passes_the_escape_character():
    """Compiled and inspected, because omitting `escape=` is invisible on Postgres in development
    and changes the result on SQLite — the reverse of the failure you would want."""
    from src.domains.personal_learning.db_models import SavedResource

    compiled = str(
        ilike_any("100%", SavedResource.title).compile(compile_kwargs={"literal_binds": True})
    )
    assert "ESCAPE" in compiled.upper()


# ---------------------------------------------------------------------------
# Every search that was unprotected
# ---------------------------------------------------------------------------


@pytest.fixture
async def pl_repo(monkeypatch):
    """In-memory SQLite with the personal-learning tables the searches below touch."""
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
        pl_models.SavedResource.__table__,
        pl_models.Flashcard.__table__,
        pl_models.GeneratedDocument.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        await session.commit()

    yield repository_module.personal_learning_repo, factory
    await engine.dispose()


async def test_saved_resource_search_does_not_treat_percent_as_a_wildcard(pl_repo):
    """The search box on the saved library, on both web and mobile."""
    repo, _ = pl_repo
    await repo.create_resource(
        {"userId": USER, "title": "Scoring 100% on finals", "sourceType": "manual"}
    )
    await repo.create_resource(
        {"userId": USER, "title": "Baking sourdough", "sourceType": "manual"}
    )

    matched, total = await repo.list_resources(USER, search="100%")

    assert total == 1
    assert [row.title for row in matched] == ["Scoring 100% on finals"]


async def test_saved_resource_search_does_not_treat_underscore_as_a_wildcard(pl_repo):
    repo, _ = pl_repo
    await repo.create_resource({"userId": USER, "title": "axb notation", "sourceType": "manual"})

    _, total = await repo.list_resources(USER, search="a_b")

    assert total == 0


async def test_saved_resource_search_still_matches_ordinary_terms(pl_repo):
    """The counterpart assertion. Escaping that broke normal search would be a worse bug than the
    one it fixed, and a test that only checks wildcards would not notice."""
    repo, _ = pl_repo
    await repo.create_resource(
        {"userId": USER, "title": "Dijkstra explained", "sourceType": "manual"}
    )
    await repo.create_resource(
        {"userId": USER, "title": "Baking sourdough", "sourceType": "manual"}
    )

    matched, total = await repo.list_resources(USER, search="dijkstra")

    assert total == 1
    assert matched[0].title == "Dijkstra explained"


async def test_a_bare_percent_matches_only_rows_containing_one(pl_repo):
    """The clearest statement of the bug: `%` alone used to match the whole table."""
    repo, _ = pl_repo
    for title in ("Scoring 100% on finals", "Baking sourdough", "Half off: 50% today"):
        await repo.create_resource({"userId": USER, "title": title, "sourceType": "manual"})

    matched, total = await repo.list_resources(USER, search="%")

    assert total == 2
    assert sorted(row.title for row in matched) == [
        "Half off: 50% today",
        "Scoring 100% on finals",
    ]
