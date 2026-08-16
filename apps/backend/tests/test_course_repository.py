"""Course queries, against a real database engine.

SQLite in memory with foreign keys enforced, matching the study-plan and flashcard repository
suites. These are grouped aggregates, a `LEFT JOIN` and an escaped `LIKE` — none of which is checked
by asserting on the Python around them, and all of which were wrong in a way that returned a
plausible answer rather than an error.

The HTTP-level behaviour is in ``test_course_api.py``, which needs Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "course-test-user"
OTHER_USER = "course-test-intruder"


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
    # Only the tables under test plus their foreign-key parents. The shared metadata is one namespace
    # for every domain and some of it uses Postgres-only column types, so creating all of it would
    # pass or fail depending on what else the run happened to import.
    tables = [
        identity_models.User.__table__,
        knowledge_models.Course.__table__,
        knowledge_models.Module.__table__,
        knowledge_models.Topic.__table__,
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


async def _course(repo, *, user_id=USER, title="Algorithms", **extra):
    return await repo.create_course({"userId": user_id, "title": title, **extra})


async def _module(repo, course, *, title="Module", order=1.0):
    return await repo.create_module({"courseId": course.id, "title": title, "order": order})


async def _topic(repo, module, *, title="Topic", order=1.0, completed=False):
    topic = await repo.create_topic({"moduleId": module.id, "title": title, "order": order})
    if completed:
        await repo.update_topic(topic.id, {"completed": True})
    return topic


# ---------------------------------------------------------------------------
# Progress totals for a page
# ---------------------------------------------------------------------------


async def test_progress_totals_counts_modules_and_topics(repo):
    course = await _course(repo)
    first = await _module(repo, course, title="One", order=1)
    second = await _module(repo, course, title="Two", order=2)
    await _topic(repo, first, title="a", completed=True)
    await _topic(repo, first, title="b")
    await _topic(repo, second, title="c")

    totals = await repo.course_progress_totals([course.id])

    assert totals[course.id] == (2, 3, 1)


async def test_progress_totals_counts_a_module_with_no_topics(repo):
    """The `LEFT JOIN`. An inner join would drop the module and undercount `moduleCount`."""
    course = await _course(repo)
    await _module(repo, course, title="Empty", order=1)

    assert await repo.course_progress_totals([course.id]) == {course.id: (1, 0, 0)}


async def test_progress_totals_does_not_multiply_modules_by_their_topics(repo):
    """`COUNT(DISTINCT module)`. Without it, one module with three topics counts as three modules."""
    course = await _course(repo)
    module = await _module(repo, course)
    for name in ("a", "b", "c"):
        await _topic(repo, module, title=name)

    modules, total, _ = (await repo.course_progress_totals([course.id]))[course.id]

    assert (modules, total) == (1, 3)


async def test_progress_totals_omits_a_course_with_no_modules(repo):
    """Absent rather than zeroed, so one place decides what "no modules" renders as."""
    course = await _course(repo)

    assert await repo.course_progress_totals([course.id]) == {}


async def test_progress_totals_keeps_courses_apart(repo):
    first = await _course(repo, title="First")
    second = await _course(repo, title="Second")
    await _topic(repo, await _module(repo, first), title="a", completed=True)
    await _topic(repo, await _module(repo, second), title="b")

    totals = await repo.course_progress_totals([first.id, second.id])

    assert totals[first.id] == (1, 1, 1)
    assert totals[second.id] == (1, 1, 0)


async def test_progress_totals_short_circuits_on_no_courses(repo):
    assert await repo.course_progress_totals([]) == {}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_matches_title_or_description(repo):
    by_title = await _course(repo, title="Graph theory")
    by_description = await _course(repo, title="Unrelated", description="covers graph traversal")
    await _course(repo, title="Statistics", description="none of it")

    found, total = await repo.list_courses(USER, where={"search": "graph"})

    assert {course.id for course in found} == {by_title.id, by_description.id}
    assert total == 2


async def test_search_is_case_insensitive(repo):
    course = await _course(repo, title="Discrete MATHEMATICS")

    found, _ = await repo.list_courses(USER, where={"search": "mathematics"})

    assert [c.id for c in found] == [course.id]


async def test_search_treats_a_percent_sign_literally(repo):
    """The escaping. Unescaped, `100%` is `%100%%` and matches every course in the library.

    A search that silently returns the wrong rows is harder to notice than one that errors, which is
    why this is asserted rather than left to the shape of the SQL.
    """
    literal = await _course(repo, title="Scoring 100% on the final")
    # Contains "100" but not "100%". Unescaped, the pattern becomes `%100%%`, which reads as
    # "anything, 100, anything" and matches this too — so without this row the test passes either way.
    await _course(repo, title="Chapter 100 review")

    found, total = await repo.list_courses(USER, where={"search": "100%"})

    assert [course.id for course in found] == [literal.id]
    assert total == 1


async def test_search_treats_an_underscore_literally(repo):
    """`_` is a single-character wildcard, so `a_b` would also match `axb`."""
    literal = await _course(repo, title="snake_case naming")
    await _course(repo, title="snakexcase naming")

    found, _ = await repo.list_courses(USER, where={"search": "snake_case"})

    assert [course.id for course in found] == [literal.id]


async def test_search_with_no_match_returns_nothing(repo):
    await _course(repo, title="Algorithms")

    found, total = await repo.list_courses(USER, where={"search": "nothing here"})

    assert found == []
    assert total == 0


# ---------------------------------------------------------------------------
# The other filters
# ---------------------------------------------------------------------------


async def test_archived_filter_separates_the_library_from_the_archive(repo):
    kept = await _course(repo, title="Active")
    shelved = await _course(repo, title="Shelved")
    await repo.update_course(shelved.id, {"archived": True})

    library, _ = await repo.list_courses(USER, where={"archived": False})
    archive, _ = await repo.list_courses(USER, where={"archived": True})

    assert [course.id for course in library] == [kept.id]
    assert [course.id for course in archive] == [shelved.id]


async def test_omitting_the_space_filter_returns_both_kinds(repo):
    """`spaceId` absent means no filter.

    It used to be forced to `None` whenever the caller left it out, which is not the same thing: a
    course belonging to a space could not be listed at all, and nothing could ask for everything.
    """
    personal = await _course(repo, title="Personal")
    shared = await _course(repo, title="Shared", spaceId="space-1")

    found, total = await repo.list_courses(USER, where={})

    assert {course.id for course in found} == {personal.id, shared.id}
    assert total == 2


async def test_space_filter_of_none_returns_only_personal_courses(repo):
    personal = await _course(repo, title="Personal")
    await _course(repo, title="Shared", spaceId="space-1")

    found, _ = await repo.list_courses(USER, where={"spaceId": None})

    assert [course.id for course in found] == [personal.id]


async def test_list_is_scoped_to_the_learner(repo):
    mine = await _course(repo, title="Mine")
    await _course(repo, user_id=OTHER_USER, title="Theirs")

    found, total = await repo.list_courses(USER, where={})

    assert [course.id for course in found] == [mine.id]
    assert total == 1


async def test_total_counts_every_match_not_just_the_page(repo):
    """The count and the page are separate queries; the count must ignore the limit."""
    for index in range(5):
        await _course(repo, title=f"Course {index}")

    found, total = await repo.list_courses(USER, where={}, skip=0, take=2)

    assert len(found) == 2
    assert total == 5


# ---------------------------------------------------------------------------
# What a card needs beyond its counts
# ---------------------------------------------------------------------------


async def test_next_topic_is_the_first_incomplete_in_outline_order(repo):
    course = await _course(repo)
    first = await _module(repo, course, title="One", order=1)
    second = await _module(repo, course, title="Two", order=2)
    await _topic(repo, first, title="Done", order=1, completed=True)
    await _topic(repo, first, title="Next up", order=2)
    await _topic(repo, second, title="Later", order=1)

    found = await repo.next_topics([course.id])

    assert found[course.id].title == "Next up"


async def test_next_topic_orders_by_module_before_topic(repo):
    """A topic ordered 1 in the second module comes after one ordered 2 in the first."""
    course = await _course(repo)
    first = await _module(repo, course, title="One", order=1)
    second = await _module(repo, course, title="Two", order=2)
    await _topic(repo, second, title="Second module, first topic", order=1)
    await _topic(repo, first, title="First module, second topic", order=2)

    found = await repo.next_topics([course.id])

    assert found[course.id].title == "First module, second topic"


async def test_next_topic_absent_when_the_course_is_finished(repo):
    """Absent, not a blank title — finished wants a different label on the card."""
    course = await _course(repo)
    await _topic(repo, await _module(repo, course), title="Only", completed=True)

    assert await repo.next_topics([course.id]) == {}


async def test_next_topic_returns_one_row_per_course(repo):
    first = await _course(repo, title="First")
    second = await _course(repo, title="Second")
    for course in (first, second):
        module = await _module(repo, course)
        await _topic(repo, module, title=f"{course.title} a", order=1)
        await _topic(repo, module, title=f"{course.title} b", order=2)

    found = await repo.next_topics([first.id, second.id])

    assert found[first.id].title == "First a"
    assert found[second.id].title == "Second a"


async def test_remaining_hours_sums_only_incomplete_topics(repo):
    course = await _course(repo)
    module = await _module(repo, course)
    done = await repo.create_topic(
        {"moduleId": module.id, "title": "Done", "order": 1, "estimatedHours": 5}
    )
    await repo.update_topic(done.id, {"completed": True})
    await repo.create_topic(
        {"moduleId": module.id, "title": "Left", "order": 2, "estimatedHours": 2}
    )

    assert await repo.remaining_hours([course.id]) == {course.id: 2.0}


async def test_remaining_hours_omits_a_course_with_no_estimates(repo):
    """Absent rather than `0`, so a client says "no estimate" instead of "nothing left"."""
    course = await _course(repo)
    await _topic(repo, await _module(repo, course), title="Unsized")

    assert await repo.remaining_hours([course.id]) == {}


async def test_card_aggregates_short_circuit_on_no_courses(repo):
    assert await repo.next_topics([]) == {}
    assert await repo.remaining_hours([]) == {}


# ---------------------------------------------------------------------------
# The library dashboard's sources
# ---------------------------------------------------------------------------


async def test_library_topic_totals_excludes_archived_courses(repo):
    """Archiving is "not now". Counting shelved work towards progress ignores that."""
    active = await _course(repo, title="Active")
    shelved = await _course(repo, title="Shelved")
    await _topic(repo, await _module(repo, active), title="a", completed=True)
    await _topic(repo, await _module(repo, active), title="b")
    await _topic(repo, await _module(repo, shelved), title="c", completed=True)
    await repo.update_course(shelved.id, {"archived": True})

    assert await repo.library_topic_totals(USER) == (2, 1)


async def test_library_topic_totals_is_scoped_to_the_learner(repo):
    theirs = await _course(repo, user_id=OTHER_USER, title="Theirs")
    await _topic(repo, await _module(repo, theirs), title="a", completed=True)

    assert await repo.library_topic_totals(USER) == (0, 0)


async def test_completed_topic_dates_excludes_completions_with_no_timestamp(repo):
    """A topic completed before `completedAt` existed has no date, and is not given one.

    `updatedAt` is not a substitute: it moves when a topic is renamed or has content generated into
    it, so reading it here would report an edit as study activity.
    """
    from datetime import UTC, datetime

    course = await _course(repo)
    module = await _module(repo, course)
    timed = await _topic(repo, module, title="Timed", order=1, completed=True)
    await _topic(repo, module, title="Untimed", order=2, completed=True)
    await repo.update_topic(timed.id, {"completedAt": datetime.now(UTC)})

    dates = await repo.completed_topic_dates(USER)

    assert len(dates) == 1


async def test_completed_hours_between_counts_only_the_window(repo):
    from datetime import UTC, datetime, timedelta

    course = await _course(repo)
    module = await _module(repo, course)
    now = datetime.now(UTC)
    inside = await repo.create_topic(
        {"moduleId": module.id, "title": "In", "order": 1, "estimatedHours": 1.5}
    )
    outside = await repo.create_topic(
        {"moduleId": module.id, "title": "Out", "order": 2, "estimatedHours": 4}
    )
    await repo.update_topic(inside.id, {"completed": True, "completedAt": now})
    await repo.update_topic(
        outside.id, {"completed": True, "completedAt": now - timedelta(days=30)}
    )

    total = await repo.completed_hours_between(
        USER, now - timedelta(days=7), now + timedelta(days=1)
    )

    assert total == 1.5


async def test_recently_completed_topics_is_newest_first_with_its_course(repo):
    from datetime import UTC, datetime, timedelta

    course = await _course(repo, title="Algorithms")
    module = await _module(repo, course)
    now = datetime.now(UTC)
    older = await _topic(repo, module, title="Older", order=1, completed=True)
    newer = await _topic(repo, module, title="Newer", order=2, completed=True)
    await repo.update_topic(older.id, {"completedAt": now - timedelta(days=2)})
    await repo.update_topic(newer.id, {"completedAt": now})

    rows = await repo.recently_completed_topics(USER, limit=5)

    assert [topic.title for topic, _course_id, _title in rows] == ["Newer", "Older"]
    # The course travels with the topic: a list of titles with no course beside them is unreadable.
    assert {title for _t, _cid, title in rows} == {"Algorithms"}


async def test_reopening_a_topic_clears_its_completion_time(repo):
    """A pending topic carrying a completion time is a row that contradicts itself.

    The activity feed and the streak both read that column, so they would keep counting a reopened
    topic as done.
    """
    from datetime import UTC, datetime

    course = await _course(repo)
    topic = await _topic(repo, await _module(repo, course), title="Done", completed=True)
    await repo.update_topic(topic.id, {"completedAt": datetime.now(UTC)})

    await repo.update_topic(topic.id, {"completed": False, "completedAt": None})

    assert await repo.completed_topic_dates(USER) == []
