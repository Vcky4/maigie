"""Every column the Learn dashboard mappers read must actually be loaded.

**This file exists because of a bug that 2212 passing tests could not see.**

The dashboard's course queries defer `Topic.content`, `Topic.objectives` and `Topic.knowledgeCheck`,
because `content` is the whole lesson body and fetching it for every topic of every course on the
page measured 2.76 s in a single statement. The first version of that change used `load_only` with
the columns `_map_course` reads — and broke `_load_featured`, which also reads `topic.summary`, with a
`DetachedInstanceError` after the session had closed.

Nothing caught it. `tests/test_learn_dashboard.py` builds its courses from `SimpleNamespace`, which
has no deferred columns, no lazy relationships and no session to detach from, so **no mapper test can
ever exercise loader configuration.** That is a reasonable design for testing composition and
degradation, and a blind spot for exactly this.

So these tests run the real repository against real ORM rows and then read the attributes **after the
session is gone**, which is when the response is actually built. A deferred column that a mapper wants
raises here rather than in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "column-loading-user"


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
        knowledge_models.Course.__table__,
        knowledge_models.Module.__table__,
        knowledge_models.Topic.__table__,
        knowledge_models.TopicSection.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        await session.commit()

    yield repository_module.knowledge_repo, factory
    await engine.dispose()


async def _course_with_topics(factory, *, course_id: str = "c1", completed_first: bool = True):
    """A course with two topics, the first completed, both carrying every heavy column."""
    from datetime import UTC, datetime

    from src.domains.knowledge import db_models as km

    async with factory() as session:
        session.add(km.Course(id=course_id, user_id=USER, title="Algorithms", archived=False))
        session.add(km.Module(id=f"{course_id}-m1", course_id=course_id, title="Basics", order=0))
        session.add(
            km.Topic(
                id=f"{course_id}-t1",
                module_id=f"{course_id}-m1",
                title="Big-O",
                order=0,
                completed=completed_first,
                completed_at=datetime.now(UTC) if completed_first else None,
                estimated_hours=1.5,
                summary="What complexity means",
                content="# The whole lesson body\n" * 50,
                objectives=["Read Big-O"],
                knowledge_check={"question": "q", "choices": []},
            )
        )
        session.add(
            km.Topic(
                id=f"{course_id}-t2",
                module_id=f"{course_id}-m1",
                title="Sorting",
                order=1,
                completed=False,
                estimated_hours=2.0,
                summary="How sorting is compared",
                content="# Another whole lesson body\n" * 50,
                objectives=["Compare sorts"],
                knowledge_check={"question": "q2", "choices": []},
            )
        )
        session.add(
            km.TopicSection(
                id=f"{course_id}-s1",
                topic_id=f"{course_id}-t1",
                title="A section",
                order=0,
                paragraphs=["text"],
            )
        )
        await session.commit()


async def test_map_course_reads_only_loaded_columns(repo):
    """`_map_course` after the session is closed, which is when the response is built."""
    from src.domains.personal_learning.services.learn_dashboard_service import _map_course

    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    courses, _ = await knowledge_repo.list_courses(
        USER, where={"archived": False}, skip=0, take=4, order={"updatedAt": "desc"}
    )

    # No session, no lazy loading available. A deferred column the mapper wants raises here.
    summary = _map_course(courses[0])

    assert summary.total_topics == 2
    assert summary.completed_topics == 1
    assert summary.module_count == 1
    assert summary.next_topic is not None
    assert summary.next_topic.title == "Sorting"


async def test_featured_reads_only_loaded_columns(repo):
    """The regression this file was written for.

    `_load_featured` reads `next_topic.summary`, which `_map_course` does not. An allow-list built
    from one mapper's needs left the other one raising `DetachedInstanceError` at response time.
    """
    from src.domains.personal_learning.services import learn_dashboard_service as svc

    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    featured = await svc._load_featured(USER)

    assert featured is not None
    assert featured.entity_type == "topic"
    assert featured.title == "Sorting"
    # The field whose absence was the bug.
    assert featured.description == "How sorting is compared"
    assert featured.total_units == 2
    assert featured.completed_units == 1


async def test_featured_reads_only_loaded_columns_when_reusing_the_course_page(repo):
    """The same, through the reuse path.

    `_load_featured` takes the course-page task and prefers a course already loaded there, so the
    rows it reads come from `list_courses`' loader options rather than `find_course_outline`'s. Both
    paths have to satisfy the same attribute reads, and only this one had the bug.
    """
    import asyncio

    from src.domains.personal_learning.services import learn_dashboard_service as svc

    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    courses_task = asyncio.create_task(svc._load_courses(USER, 4))
    featured = await svc._load_featured(USER, courses_task)
    await courses_task

    assert featured is not None
    assert featured.description == "How sorting is compared"
    assert featured.title == "Sorting"


async def test_the_heavy_columns_really_are_deferred(repo):
    """The other half: if nothing is deferred, the tests above pass and the query is slow again.

    Asserted through `sqlalchemy.inspect`'s unloaded set rather than by timing, which would be
    flaky, and rather than by SQL string matching, which would not survive a column rename.
    """
    from sqlalchemy import inspect as sa_inspect

    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    courses, _ = await knowledge_repo.list_courses(
        USER, where={"archived": False}, skip=0, take=4, order={"updatedAt": "desc"}
    )
    topic = courses[0].modules[0].topics[0]
    unloaded = sa_inspect(topic).unloaded

    assert "content" in unloaded, "Topic.content is the whole lesson body and must not be fetched"
    assert "objectives" in unloaded
    assert "knowledge_check" in unloaded
    # The sections relationship is `lazy="selectin"` by default and is suppressed with `noload`,
    # which yields an empty collection rather than an unloaded one — so it is asserted by value.
    # Empty rather than raising is what makes this safe: a caller that reads it gets nothing back
    # instead of an error, which is the right trade for a field no dashboard mapper touches.
    assert courses[0].modules[0].topics[0].sections == []
    # While everything the mappers read is present.
    for name in ("id", "title", "completed", "estimated_hours", "summary", "order"):
        assert name not in unloaded, f"{name} is read by a dashboard mapper and must be loaded"


async def test_recently_completed_topics_defers_the_heavy_columns(repo):
    from sqlalchemy import inspect as sa_inspect

    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    rows = await knowledge_repo.recently_completed_topics(USER, limit=1)

    assert rows, "a completed topic should be found"
    topic, course_id, course_title = rows[0]
    assert course_id == "c1"
    assert course_title == "Algorithms"

    unloaded = sa_inspect(topic).unloaded
    assert "content" in unloaded
    assert "objectives" in unloaded
    assert "knowledge_check" in unloaded
    assert topic.sections == []


async def test_find_course_outline_omits_sections_but_keeps_what_the_mapper_reads(repo):
    knowledge_repo, factory = repo
    await _course_with_topics(factory)

    course = await knowledge_repo.find_course_outline("c1", USER)

    assert course is not None
    assert course.modules[0].topics[0].sections == []

    # `find_course_with_modules` is the variant that keeps sections, because `CourseResponse`
    # publishes them through `TopicResponse`; this one is for callers that want the shape of a
    # course rather than its content. The contrast is the assertion: one method must load them and
    # the other must not, and neither may quietly change into the other.
    with_sections = await knowledge_repo.find_course_with_modules("c1", USER)
    assert with_sections is not None
    assert len(with_sections.modules[0].topics[0].sections) == 1
