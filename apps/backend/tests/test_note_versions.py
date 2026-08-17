"""Note versions and the tag catalogue, against a real database engine.

SQLite in memory, following ``test_flashcard_repository.py``: these run on every invocation
instead of skipping whenever ``DATABASE_URL`` is unset, and what they check is query and
transaction behaviour rather than the Python wrapped around it.

The subject is a table that existed for a long time with neither a producer nor a consumer.
`NoteHistory` held zero rows and nothing read it, which made `POST /notes/{id}/retake` a
one-way door: it sends a learner's own prose to a model and writes the rewrite over the only
copy. Most of what follows is about that write, and about the ways a version log can be
useless — filling up with duplicates, losing the original when you restore, or reverting a
rename nobody asked to revert.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "note-test-user"
OTHER_USER = "note-test-intruder"


@pytest.fixture
async def repo(monkeypatch):
    """A repository bound to a fresh in-memory database, with two learners in it."""
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.knowledge import db_models as knowledge_models
    from src.domains.personal_learning import db_models as pl_models
    from src.domains.personal_learning import repository as repository_module
    from src.shared.database.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # SQLite ignores foreign keys unless told otherwise, and the cascade from `Note` to
    # `NoteHistory` is one of the things asserted below.
    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Only the tables under test plus the parents their foreign keys point at. `Note`
    # references `Course` and `Topic`, so those come along: the columns are null in these
    # tests, but a foreign key cannot even be emitted unless its target is in the metadata.
    tables = [
        identity_models.User.__table__,
        knowledge_models.Course.__table__,
        knowledge_models.Module.__table__,
        knowledge_models.Topic.__table__,
        pl_models.Note.__table__,
        pl_models.NoteTag.__table__,
        pl_models.NoteAttachment.__table__,
        pl_models.NoteHistory.__table__,
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


async def _note(repo, *, title="Vectors", content="my own words", user_id=USER, archived=False):
    return await repo.create_note(
        {"userId": user_id, "title": title, "content": content, "archived": archived}
    )


# ---------------------------------------------------------------------------
# Snapshots on write
# ---------------------------------------------------------------------------


async def test_editing_content_records_the_previous_version(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="first draft")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "second draft"})

    versions, total = await repo.list_note_history(note.id, USER)
    assert total == 1
    assert versions[0].content == "first draft"
    assert versions[0].title == "Vectors"


async def test_a_write_that_does_not_change_content_records_nothing(repo):
    """An autosaving editor sends the whole note on every pause.

    Snapshotting because `content` was present in the body would fill the log with identical
    entries and bury the version worth going back to.
    """
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="unchanged")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "unchanged"})
    await note_service.update_note(user_id=USER, note_id=note.id, data={"title": "Renamed"})

    _, total = await repo.list_note_history(note.id, USER)
    assert total == 0


async def test_a_blank_note_getting_its_first_content_is_still_recorded(repo):
    """ "It was empty here" is a fact the list needs.

    Skipping the empty snapshot would make the first real draft look like the original, which
    is the one thing a version list must not do.
    """
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content=None)
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "a start"})

    versions, total = await repo.list_note_history(note.id, USER)
    assert total == 1
    assert versions[0].content is None


async def test_versions_come_back_newest_first(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="v1")
    for content in ("v2", "v3", "v4"):
        await note_service.update_note(user_id=USER, note_id=note.id, data={"content": content})

    versions, total = await repo.list_note_history(note.id, USER)
    assert total == 3
    assert [v.content for v in versions] == ["v3", "v2", "v1"]


async def test_history_is_paginated(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="v0")
    for index in range(5):
        await note_service.update_note(
            user_id=USER, note_id=note.id, data={"content": f"v{index + 1}"}
        )

    page_one, total = await note_service.list_history(user_id=USER, note_id=note.id, size=2)
    page_two, _ = await note_service.list_history(user_id=USER, note_id=note.id, page=2, size=2)
    assert total == 5
    assert len(page_one) == 2
    assert {v.id for v in page_one}.isdisjoint({v.id for v in page_two})


# ---------------------------------------------------------------------------
# Retake — the write the table exists for
# ---------------------------------------------------------------------------


async def test_an_ai_retake_records_the_learners_own_prose_first(repo, monkeypatch):
    from src.domains.intelligence.reasoning import llm
    from src.domains.personal_learning.services import note_service

    async def _rewrite(*_args, **_kwargs):
        return "## A Tidier Version\n\nBullet points."

    monkeypatch.setattr(llm, "generate_content", _rewrite)

    note = await _note(repo, content="what I actually wrote")
    updated = await note_service.retake_note(user_id=USER, note_id=note.id)

    assert updated.content == "## A Tidier Version\n\nBullet points."
    versions, total = await repo.list_note_history(note.id, USER)
    assert total == 1
    assert versions[0].content == "what I actually wrote"


async def test_an_empty_rewrite_is_refused_rather_than_written(repo, monkeypatch):
    """A model returning nothing used to erase the note and report success."""
    from src.domains.intelligence.reasoning import llm
    from src.domains.personal_learning.services import note_service
    from src.shared.exceptions import ValidationError

    async def _nothing(*_args, **_kwargs):
        return "   "

    monkeypatch.setattr(llm, "generate_content", _nothing)

    note = await _note(repo, content="still here")
    with pytest.raises(ValidationError):
        await note_service.retake_note(user_id=USER, note_id=note.id)

    unchanged = await repo.find_note(note.id, USER)
    assert unchanged.content == "still here"
    _, total = await repo.list_note_history(note.id, USER)
    assert total == 0


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


async def test_restoring_puts_the_content_back(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="original")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "replacement"})
    versions, _ = await repo.list_note_history(note.id, USER)

    restored = await note_service.restore_version(
        user_id=USER, note_id=note.id, version_id=versions[0].id
    )
    assert restored.content == "original"


async def test_restoring_is_itself_undoable(repo):
    """Restoring overwrites, so the current content is snapshotted before it goes.

    A version log whose use loses data is worse than no version log.
    """
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="original")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "replacement"})
    versions, _ = await repo.list_note_history(note.id, USER)

    await note_service.restore_version(user_id=USER, note_id=note.id, version_id=versions[0].id)

    after, total = await repo.list_note_history(note.id, USER)
    assert total == 2
    assert after[0].content == "replacement"


async def test_restoring_does_not_revert_a_rename(repo):
    """Titles are snapshotted to label a version, not to be restored.

    Snapshots are taken when *content* changes, so a learner who renamed a note without editing
    it has no snapshot carrying the new name. Restoring content would then silently undo a rename
    they never asked to reverse.
    """
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, title="Draft", content="original")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "replacement"})
    await note_service.update_note(user_id=USER, note_id=note.id, data={"title": "Final title"})
    versions, _ = await repo.list_note_history(note.id, USER)

    restored = await note_service.restore_version(
        user_id=USER, note_id=note.id, version_id=versions[-1].id
    )
    assert restored.content == "original"
    assert restored.title == "Final title"


async def test_restoring_the_same_content_records_nothing_new(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="original")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "replacement"})
    versions, _ = await repo.list_note_history(note.id, USER)
    await note_service.restore_version(user_id=USER, note_id=note.id, version_id=versions[0].id)

    _, before = await repo.list_note_history(note.id, USER)
    await note_service.restore_version(user_id=USER, note_id=note.id, version_id=versions[0].id)
    _, after = await repo.list_note_history(note.id, USER)
    assert after == before


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_another_learner_cannot_read_a_notes_history(repo):
    from src.domains.personal_learning.services import note_service
    from src.shared.exceptions import NotFoundError

    note = await _note(repo, content="private")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "edited"})

    with pytest.raises(NotFoundError):
        await note_service.list_history(user_id=OTHER_USER, note_id=note.id)


async def test_a_version_of_another_note_cannot_be_restored_into_this_one(repo):
    """The version id is scoped by note as well as by owner.

    Without both, a learner could paste another note's content over this one by id — their own
    data, so no authorization failure, and still not something the API offers.
    """
    from src.domains.personal_learning.services import note_service
    from src.shared.exceptions import NotFoundError

    first = await _note(repo, title="One", content="one-original")
    second = await _note(repo, title="Two", content="two-original")
    await note_service.update_note(user_id=USER, note_id=first.id, data={"content": "one-edited"})
    versions, _ = await repo.list_note_history(first.id, USER)

    with pytest.raises(NotFoundError):
        await note_service.restore_version(
            user_id=USER, note_id=second.id, version_id=versions[0].id
        )


async def test_deleting_a_note_takes_its_versions_with_it(repo):
    from src.domains.personal_learning.services import note_service

    note = await _note(repo, content="original")
    await note_service.update_note(user_id=USER, note_id=note.id, data={"content": "edited"})

    assert await note_service.delete_note(user_id=USER, note_id=note.id) is True

    _, total = await repo.list_note_history(note.id, USER)
    assert total == 0


# ---------------------------------------------------------------------------
# Tag catalogue
# ---------------------------------------------------------------------------


async def test_the_tag_catalogue_counts_the_library_not_a_page(repo):
    from src.domains.personal_learning.services import note_service

    for index in range(3):
        note = await _note(repo, title=f"Note {index}")
        await repo.create_note_tags(note.id, ["algebra"])
    only = await _note(repo, title="Outlier")
    await repo.create_note_tags(only.id, ["topology"])

    catalogue = await note_service.list_tags(user_id=USER)
    assert catalogue == [{"tag": "algebra", "count": 3}, {"tag": "topology", "count": 1}]


async def test_the_tag_catalogue_matches_what_the_default_list_shows(repo):
    """Archived notes are excluded, because the default note list excludes them.

    A chip's count has to be the number of notes the chip would show; otherwise clicking a tag
    marked "4" returns three results.
    """
    from src.domains.personal_learning.services import note_service

    live = await _note(repo, title="Live")
    await repo.create_note_tags(live.id, ["algebra"])
    archived = await _note(repo, title="Archived", archived=True)
    await repo.create_note_tags(archived.id, ["algebra"])

    assert await note_service.list_tags(user_id=USER) == [{"tag": "algebra", "count": 1}]
    assert await note_service.list_tags(user_id=USER, archived=True) == [
        {"tag": "algebra", "count": 1}
    ]


async def test_the_tag_catalogue_is_per_learner(repo):
    from src.domains.personal_learning.services import note_service

    mine = await _note(repo, title="Mine")
    await repo.create_note_tags(mine.id, ["algebra"])
    theirs = await _note(repo, title="Theirs", user_id=OTHER_USER)
    await repo.create_note_tags(theirs.id, ["algebra"])

    assert await note_service.list_tags(user_id=USER) == [{"tag": "algebra", "count": 1}]


# ---------------------------------------------------------------------------
# Attachments and storage
# ---------------------------------------------------------------------------


async def test_removing_an_uploaded_attachment_deletes_the_stored_file(repo, monkeypatch):
    """The row used to go and the object used to stay, unreferenced and permanent."""
    from src.domains.personal_learning.services import note_service
    from src.shared.infrastructure import storage

    deleted: list[str] = []

    async def _delete(url):
        deleted.append(url)
        return True

    monkeypatch.setattr(storage.storage_service, "delete", _delete)
    monkeypatch.setattr(storage.storage_service, "owns_url", lambda url: "cdn.example" in url)

    note = await _note(repo)
    attachment = await note_service.add_attachment(
        user_id=USER,
        note_id=note.id,
        data={"filename": "slides.pdf", "url": "https://cdn.example/note-attachments/a/slides.pdf"},
    )

    assert (
        await note_service.remove_attachment(
            user_id=USER, note_id=note.id, attachment_id=attachment.id
        )
        is True
    )
    assert deleted == ["https://cdn.example/note-attachments/a/slides.pdf"]
    assert await repo.find_attachment(attachment.id, note.id) is None


async def test_a_pasted_link_is_not_deleted_from_storage(repo, monkeypatch):
    """The JSON route accepts any URL, so an attachment can be somebody else's page.

    Issuing a delete against our own storage zone at that page's path is at best a wasted
    request and at worst a delete of an unrelated object that happens to share the path.
    """
    from src.domains.personal_learning.services import note_service
    from src.shared.infrastructure import storage

    deleted: list[str] = []

    async def _delete(url):
        deleted.append(url)
        return True

    monkeypatch.setattr(storage.storage_service, "delete", _delete)
    monkeypatch.setattr(storage.storage_service, "owns_url", lambda url: "cdn.example" in url)

    note = await _note(repo)
    attachment = await note_service.add_attachment(
        user_id=USER,
        note_id=note.id,
        data={"filename": "paper", "url": "https://arxiv.org/abs/1706.03762"},
    )

    await note_service.remove_attachment(user_id=USER, note_id=note.id, attachment_id=attachment.id)
    assert deleted == []


async def test_deleting_a_note_cleans_up_its_uploaded_files(repo, monkeypatch):
    """`Note -> NoteAttachment` cascades in the database; storage does not cascade."""
    from src.domains.personal_learning.services import note_service
    from src.shared.infrastructure import storage

    deleted: list[str] = []

    async def _delete(url):
        deleted.append(url)
        return True

    monkeypatch.setattr(storage.storage_service, "delete", _delete)
    monkeypatch.setattr(storage.storage_service, "owns_url", lambda url: "cdn.example" in url)

    note = await _note(repo)
    for name in ("one.pdf", "two.pdf"):
        await note_service.add_attachment(
            user_id=USER,
            note_id=note.id,
            data={"filename": name, "url": f"https://cdn.example/note-attachments/{name}"},
        )
    await note_service.add_attachment(
        user_id=USER,
        note_id=note.id,
        data={"filename": "link", "url": "https://example.org/page"},
    )

    await note_service.delete_note(user_id=USER, note_id=note.id)

    assert sorted(deleted) == [
        "https://cdn.example/note-attachments/one.pdf",
        "https://cdn.example/note-attachments/two.pdf",
    ]
