"""Diagrams and equations kept from a study session.

SQLite in memory with foreign keys enforced, following `test_course_repository.py`. The service layer is
what these exercise, because that is where the two rules live: a row must contain something drawable, and a
failure to store must never propagate to a caller who has already delivered the visual and charged for it.

The behaviour being pinned is a fix for a feature that generated output and kept none. Two producers — the
`study_show_visual` tool and `POST /gemini-live/study/diagram` — handed `{mermaid, display_math, caption}` to
the browser, which put it in an in-memory map nothing read, with no renderer installed anywhere. So the
tests worth writing are less about the happy path than about the two ways this could go wrong again: storing
an empty row (which draws a blank panel and reads as broken), and letting a storage problem take down a
conversation or turn a paid-for diagram into an error.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "illustration-user"
OTHER_USER = "illustration-intruder"


@pytest.fixture
async def env(monkeypatch):
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.knowledge import db_models as knowledge_models
    from src.domains.knowledge import repository as repository_module
    from src.domains.knowledge.services import illustration_service
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
        # `Topic.sections` is `lazy="selectin"`, so every topic read queries this table whether or not a
        # test mentions a section. Omitting it breaks the ownership check rather than skipping coverage.
        knowledge_models.TopicSection.__table__,
        knowledge_models.TopicIllustration.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        session.add(identity_models.User(id=OTHER_USER, email="intruder@example.com"))
        await session.commit()

    repo = repository_module.knowledge_repo
    course = await repo.create_course({"userId": USER, "title": "Graphs"})
    module = await repo.create_module({"courseId": course.id, "title": "Traversal", "order": 1.0})
    topic = await repo.create_topic({"moduleId": module.id, "title": "BFS", "order": 1.0})

    other_course = await repo.create_course({"userId": OTHER_USER, "title": "Theirs"})
    other_module = await repo.create_module(
        {"courseId": other_course.id, "title": "M", "order": 1.0}
    )
    other_topic = await repo.create_topic({"moduleId": other_module.id, "title": "T", "order": 1.0})

    yield {
        "service": illustration_service,
        "repo": repo,
        "topic": topic,
        "other_topic": other_topic,
    }
    await engine.dispose()


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


async def test_a_diagram_is_kept_and_readable(env):
    service, topic = env["service"], env["topic"]

    stored_id = await service.record(
        USER,
        topic_id=topic.id,
        mermaid="graph TD; A-->B;",
        display_math=None,
        caption="How BFS expands",
        source=service.SOURCE_TUTOR,
    )
    assert stored_id

    rows = await service.list_for_topic(USER, topic_id=topic.id)
    assert len(rows) == 1
    assert rows[0].mermaid == "graph TD; A-->B;"
    assert rows[0].caption == "How BFS expands"
    assert rows[0].source == "tutor"
    # Absent rather than empty string: the client renders a block per field it has, and an empty maths
    # block would draw a bordered panel containing nothing.
    assert rows[0].display_math is None


async def test_maths_alone_is_enough(env):
    """A row does not need a diagram. `display_math` alone answers some questions."""
    service, topic = env["service"], env["topic"]

    assert await service.record(
        USER,
        topic_id=topic.id,
        mermaid=None,
        display_math=r"O(V + E)",
        caption=None,
    )
    rows = await service.list_for_topic(USER, topic_id=topic.id)
    assert rows[0].mermaid is None
    assert rows[0].display_math == "O(V + E)"


async def test_a_row_with_nothing_drawable_is_refused(env):
    """The defect this guards is a blank panel, which reads as broken rather than absent.

    Whitespace counts as empty: the model returns `"  "` for a field it had nothing for, and a row holding
    two spaces is indistinguishable on screen from a row holding nothing.
    """
    service, topic = env["service"], env["topic"]

    assert (
        await service.record(
            USER, topic_id=topic.id, mermaid="  ", display_math="\n", caption="nothing here"
        )
        is None
    )
    assert await service.list_for_topic(USER, topic_id=topic.id) == []


async def test_an_unknown_source_falls_back_to_tutor(env):
    """`source` is a plain string column, so the service is the only thing narrowing it."""
    service, topic = env["service"], env["topic"]

    await service.record(
        USER,
        topic_id=topic.id,
        mermaid="graph TD; A-->B;",
        display_math=None,
        caption=None,
        source="whatever",
    )
    rows = await service.list_for_topic(USER, topic_id=topic.id)
    assert rows[0].source == "tutor"


async def test_a_storage_failure_is_swallowed_rather_than_raised(env, monkeypatch):
    """The one case that must not raise.

    Callers are a live voice relay mid-turn and a route that has already charged 80 credits. The learner has
    the diagram on screen either way, so the choice is between losing the stored copy and replacing a working
    visual with an error. Losing the copy is strictly better, and this pins it so a later refactor does not
    "tidy up" the swallow.
    """
    service, topic, repo = env["service"], env["topic"], env["repo"]

    async def boom(_data):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(repo, "create_topic_illustration", boom)

    assert (
        await service.record(
            USER, topic_id=topic.id, mermaid="graph TD; A-->B;", display_math=None, caption=None
        )
        is None
    )


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_listing_another_learners_topic_is_refused(env):
    """Refused through `check_topic_ownership`, the same as every other topic read.

    Asserted as `403`, which is what that helper actually raises for a topic belonging to somebody else — it
    reserves `404` for a topic that does not exist. That is at odds with §14.2 of the integration plan, which
    asks new endpoints to answer `404` for another learner's entity so an id cannot be probed for existence.

    The inconsistency is pre-existing and shared by every topic route, so it is pinned here rather than
    quietly diverged from: making this one route answer `404` would leave two different answers to the same
    question inside one domain, and changing the helper changes the contract of a dozen shipped endpoints.
    Recorded as an open question rather than decided in a test file.
    """
    from src.shared.exceptions import ForbiddenError

    service, other_topic = env["service"], env["other_topic"]

    with pytest.raises(ForbiddenError):
        await service.list_for_topic(USER, topic_id=other_topic.id)


async def test_listing_a_topic_that_does_not_exist_is_not_found(env):
    """The other half of the contract, so the two are not confused for each other later."""
    from src.shared.exceptions import NotFoundError

    service = env["service"]

    with pytest.raises(NotFoundError):
        await service.list_for_topic(USER, topic_id="no-such-topic")


async def test_recording_against_another_learners_topic_is_refused(env):
    """`record_checked` is the entry point for a caller that has not already resolved the topic.

    `record` itself does not check, deliberately: both real callers have just resolved the topic and a
    second check would re-ask an answered question. That makes this test the guard on the distinction.
    """
    service, other_topic = env["service"], env["other_topic"]

    with pytest.raises(Exception):
        await service.record_checked(
            USER,
            topic_id=other_topic.id,
            mermaid="graph TD; A-->B;",
            display_math=None,
            caption=None,
        )


async def test_one_learners_visuals_are_invisible_to_another(env):
    """Why the table carries `userId` at all.

    Classrooms assign courses to their members, so several learners study one topic. A diagram generated
    inside one learner's conversation was shaped by what *they* were stuck on, and it has no business
    appearing in somebody else's lesson.
    """
    service, repo, topic = env["service"], env["repo"], env["topic"]

    await service.record(
        USER, topic_id=topic.id, mermaid="graph TD; mine-->only;", display_math=None, caption=None
    )
    # Written directly, since the other learner does not own this topic and the service would refuse.
    await repo.create_topic_illustration(
        {"topicId": topic.id, "userId": OTHER_USER, "mermaid": "graph TD; theirs-->only;"}
    )

    mine = await service.list_for_topic(USER, topic_id=topic.id)
    assert len(mine) == 1
    assert mine[0].mermaid == "graph TD; mine-->only;"


# ---------------------------------------------------------------------------
# Ordering and deletion
# ---------------------------------------------------------------------------


async def test_newest_first(env):
    """Opposite to check attempts, and deliberately so.

    The first attempt at a check is the one that measures understanding, so those read oldest first. A
    diagram carries no such ordering; the most recent is what a learner returning to a lesson wants.
    """
    service, topic = env["service"], env["topic"]

    for label in ("first", "second", "third"):
        await service.record(
            USER,
            topic_id=topic.id,
            mermaid=f"graph TD; {label};",
            display_math=None,
            caption=label,
        )

    rows = await service.list_for_topic(USER, topic_id=topic.id)
    assert [row.caption for row in rows] == ["third", "second", "first"]


async def test_delete_removes_only_the_owners_row(env):
    service, repo, topic = env["service"], env["repo"], env["topic"]

    mine = await service.record(
        USER, topic_id=topic.id, mermaid="graph TD; A-->B;", display_math=None, caption=None
    )
    theirs = await repo.create_topic_illustration(
        {"topicId": topic.id, "userId": OTHER_USER, "mermaid": "graph TD; C-->D;"}
    )

    # Another learner's id reports the same "nothing removed" as an id that does not exist, which is what
    # lets the route answer 404 for both without distinguishing them.
    assert await service.delete(USER, illustration_id=theirs.id) is False
    assert await service.delete(USER, illustration_id="no-such-id") is False
    assert await service.delete(USER, illustration_id=str(mine)) is True
    assert await service.list_for_topic(USER, topic_id=topic.id) == []


async def test_deleting_the_topic_takes_its_illustrations(env):
    """`ondelete="CASCADE"`, not `SET NULL`.

    Notes and resources detach from a deleted course because they are the learner's own writing and survive
    it. A diagram of a deleted lesson illustrates nothing, so it goes with it.
    """
    service, repo, topic = env["service"], env["repo"], env["topic"]

    await service.record(
        USER, topic_id=topic.id, mermaid="graph TD; A-->B;", display_math=None, caption=None
    )
    await repo.delete_topic(topic.id)

    assert await repo.list_topic_illustrations(topic.id, USER) == []
