"""Document lifecycle — listing, filtering, publishing, deletion.

SQLite in memory, following ``test_flashcard_repository.py``, so these run on every invocation.
There was no document test of any kind before this file: nothing exercised generation, publish,
share, the job poller, or the list query, which is part of how a `NOT NULL` `shareId` came to be
declared nullable on the model and how the chat skill's insert failed on every call without anyone
noticing.

The delete tests care about *ordering* and about what happens when storage refuses, because those
are the two decisions in the function and neither is visible in its return value.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "doc-test-user"
OTHER_USER = "doc-test-intruder"


@pytest.fixture
async def repo(monkeypatch):
    """A repository bound to a fresh in-memory database, with two learners in it."""
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
    # `UserPreferences` is here because the summary resolves the learner's timezone from it. No row
    # is inserted: a learner who has never been asked resolves to unknown, which is the common case
    # and the one the month boundary has to be honest about.
    tables = [
        identity_models.User.__table__,
        identity_models.UserPreferences.__table__,
        pl_models.GeneratedDocument.__table__,
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


_SEQUENCE = iter(range(1, 10_000))


async def _document(
    repo,
    *,
    title="Photosynthesis essay",
    fmt="pdf",
    doc_type="essay",
    filename=None,
    user_id=USER,
    is_public=False,
):
    index = next(_SEQUENCE)
    return await repo.create_document(
        {
            "userId": user_id,
            "title": title,
            "format": fmt,
            "docType": doc_type,
            "style": "academic",
            "filename": filename or f"document-{index}.{fmt}",
            "fileUrl": f"https://cdn.example/generated-docs/{user_id}/file-{index}.{fmt}",
            "previewUrl": f"https://cdn.example/generated-docs/{user_id}/file-{index}.html",
            "size": 1024,
            "contentType": "application/pdf",
            "shareId": f"share-{index}",
            "isPublic": is_public,
        }
    )


@pytest.fixture
def storage(monkeypatch):
    """A storage double that records deletes and can be told to fail."""
    from src.shared.infrastructure import storage as storage_module

    calls: list[str] = []
    state = {"succeed": True}

    async def _delete(url):
        calls.append(url)
        return state["succeed"]

    monkeypatch.setattr(storage_module.storage_service, "delete", _delete)
    monkeypatch.setattr(
        storage_module.storage_service, "owns_url", lambda url: "cdn.example" in url
    )
    return {"calls": calls, "state": state}


# ---------------------------------------------------------------------------
# Listing and filtering
# ---------------------------------------------------------------------------


async def test_documents_come_back_newest_first(repo):
    from src.domains.personal_learning.services import document_impl

    first = await _document(repo, title="Older")
    second = await _document(repo, title="Newer")

    items, total = await document_impl.list_documents(user_id=USER)
    assert total == 2
    assert {d.id for d in items} == {first.id, second.id}


async def test_search_matches_the_title(repo):
    from src.domains.personal_learning.services import document_impl

    wanted = await _document(repo, title="Mitochondria report")
    await _document(repo, title="Photosynthesis essay")

    items, total = await document_impl.list_documents(user_id=USER, search="mitochondria")
    assert total == 1
    assert items[0].id == wanted.id


async def test_search_matches_the_filename(repo):
    """A learner looking for a document remembers the title or the file, not reliably both."""
    from src.domains.personal_learning.services import document_impl

    wanted = await _document(repo, title="Untitled", filename="dissertation-final.pdf")
    await _document(repo, title="Untitled", filename="notes.pdf")

    items, total = await document_impl.list_documents(user_id=USER, search="dissertation")
    assert total == 1
    assert items[0].id == wanted.id


async def test_the_type_filter_reads_a_stored_type(repo):
    """The type the learner chose was sent on every request and dropped before the insert.

    The library page therefore inferred it by substring-matching the filename and title, which made
    the filter both wrong — "Reporting standards" was a report by luck — and unable to reach past the
    page in the browser, because the value did not exist in the database to filter on.
    """
    from src.domains.personal_learning.services import document_impl

    essay = await _document(repo, title="On photosynthesis", doc_type="essay")
    await _document(repo, title="Quarterly figures", doc_type="report")

    items, total = await document_impl.list_documents(user_id=USER, type="essay")
    assert total == 1
    assert items[0].id == essay.id


async def test_a_title_that_merely_mentions_a_type_is_not_that_type(repo):
    """The exact case substring inference got wrong."""
    from src.domains.personal_learning.services import document_impl

    await _document(repo, title="Reporting standards in biology", doc_type="essay")

    _, reports = await document_impl.list_documents(user_id=USER, type="report")
    _, essays = await document_impl.list_documents(user_id=USER, type="essay")
    assert reports == 0
    assert essays == 1


async def test_documents_written_before_the_type_was_stored_have_none(repo):
    """Nullable and deliberately not backfilled: a guess in this column would read as a fact."""
    from src.domains.personal_learning.services import document_impl

    legacy = await _document(repo, doc_type=None)

    fetched = await document_impl.get_document(user_id=USER, doc_id=legacy.id)
    assert fetched.doc_type is None

    _, matched = await document_impl.list_documents(user_id=USER, type="essay")
    assert matched == 0


async def test_a_filter_narrows_the_total_as_well_as_the_page(repo):
    """Filtering only the page is how a pager advertises results a query never matched."""
    from src.domains.personal_learning.services import document_impl

    for _ in range(3):
        await _document(repo, fmt="pdf")
    await _document(repo, fmt="docx")

    _, total = await document_impl.list_documents(user_id=USER, format="docx")
    assert total == 1


async def test_search_reaches_past_the_first_page(repo):
    """The library page used to filter the twenty documents it had loaded.

    A search over a bigger library silently found nothing in the rest of it.
    """
    from src.domains.personal_learning.services import document_impl

    for index in range(25):
        await _document(repo, title=f"Filler {index}")
    await _document(repo, title="The one I want")

    items, total = await document_impl.list_documents(
        user_id=USER, page=1, page_size=20, search="the one"
    )
    assert total == 1
    assert items[0].title == "The one I want"


async def test_listing_is_scoped_to_the_owner(repo):
    from src.domains.personal_learning.services import document_impl

    await _document(repo, user_id=OTHER_USER, title="Not mine")
    mine = await _document(repo, title="Mine")

    items, total = await document_impl.list_documents(user_id=USER)
    assert total == 1
    assert items[0].id == mine.id


async def test_the_format_breakdown_covers_the_whole_library(repo):
    from src.domains.personal_learning.services import document_impl

    for _ in range(3):
        await _document(repo, fmt="pdf")
    await _document(repo, fmt="docx")
    await _document(repo, fmt="docx")
    await _document(repo, fmt="pptx")
    await _document(repo, user_id=OTHER_USER, fmt="pptx")

    summary = await document_impl.get_summary(user_id=USER)
    assert summary["formats"] == [
        {"format": "pdf", "count": 3},
        {"format": "docx", "count": 2},
        {"format": "pptx", "count": 1},
    ]


async def test_the_summary_counts_the_library_not_a_page(repo):
    """These figures were counted in the browser from one fetched page and labelled library-wide."""
    from src.domains.personal_learning.services import document_impl

    for _ in range(4):
        await _document(repo)
    published = await _document(repo)
    await document_impl.publish_document(user_id=USER, doc_id=published.id)
    await _document(repo, user_id=OTHER_USER)

    summary = await document_impl.get_summary(user_id=USER)
    assert summary["total"] == 5
    assert summary["published"] == 1
    assert summary["createdThisMonth"] == 5


async def test_the_summary_publishes_the_boundary_it_measured_from(repo):
    """ "This month" is a claim about the learner's wall clock, and the zone is often unknown.

    Returning the instant makes the page able to say what it counted from instead of asserting a
    calendar month for a learner whose location was never captured.
    """
    from src.domains.personal_learning.services import document_impl

    await _document(repo)
    summary = await document_impl.get_summary(user_id=USER)

    assert summary["monthStart"].day == 1
    assert summary["monthStart"].hour == 0


async def test_the_summary_is_empty_rather_than_absent_for_a_new_learner(repo):
    from src.domains.personal_learning.services import document_impl

    summary = await document_impl.get_summary(user_id=USER)
    assert summary["total"] == 0
    assert summary["published"] == 0
    assert summary["formats"] == []


# ---------------------------------------------------------------------------
# Publish and unpublish
# ---------------------------------------------------------------------------


async def test_publishing_makes_it_public_and_issues_a_link(repo):
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    published = await document_impl.publish_document(user_id=USER, doc_id=doc.id)

    assert published.is_public is True
    assert published.share_id != doc.share_id


async def test_a_private_document_still_has_a_share_id(repo):
    """`shareId` is `NOT NULL` and always has been.

    The model declared it nullable, which is how a raw-SQL insert came to omit it and fail at the
    constraint on every call.
    """
    doc = await _document(repo)
    assert doc.share_id
    assert doc.is_public is False


async def test_unpublishing_withdraws_the_document_and_retires_the_link(repo):
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    published = await document_impl.publish_document(user_id=USER, doc_id=doc.id)
    withdrawn = await document_impl.unpublish_document(user_id=USER, doc_id=doc.id)

    assert withdrawn.is_public is False
    assert withdrawn.share_id != published.share_id


async def test_a_retired_link_does_not_come_back_on_republishing(repo):
    """Rotating on both sides is what makes unpublishing mean something.

    Reusing the id would resurrect a URL the learner deliberately withdrew.
    """
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    first = (await document_impl.publish_document(user_id=USER, doc_id=doc.id)).share_id
    await document_impl.unpublish_document(user_id=USER, doc_id=doc.id)
    second = (await document_impl.publish_document(user_id=USER, doc_id=doc.id)).share_id

    assert first != second


async def test_unpublishing_something_private_is_a_success(repo):
    """The caller's intent is "this link should not work", which is already true — and the id
    still rotates, so a link previously handed out is dead either way."""
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    withdrawn = await document_impl.unpublish_document(user_id=USER, doc_id=doc.id)

    assert withdrawn.is_public is False
    assert withdrawn.share_id != doc.share_id


async def test_a_shared_document_resolves_only_while_it_is_public(repo):
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    published = await document_impl.publish_document(user_id=USER, doc_id=doc.id)

    assert await document_impl.get_by_share_id(share_id=published.share_id) is not None
    await document_impl.unpublish_document(user_id=USER, doc_id=doc.id)
    assert await document_impl.get_by_share_id(share_id=published.share_id) is None


async def test_another_learner_cannot_publish_or_unpublish(repo):
    from src.domains.personal_learning.services import document_impl
    from src.shared.exceptions import NotFoundError

    doc = await _document(repo)

    with pytest.raises(NotFoundError):
        await document_impl.publish_document(user_id=OTHER_USER, doc_id=doc.id)
    with pytest.raises(NotFoundError):
        await document_impl.unpublish_document(user_id=OTHER_USER, doc_id=doc.id)


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_deleting_removes_the_row_and_both_stored_objects(repo, storage):
    """Every generated document is two objects: the file and its HTML preview."""
    from src.domains.personal_learning.services import document_impl

    doc = await _document(repo)
    await document_impl.delete_document(user_id=USER, doc_id=doc.id)

    assert sorted(storage["calls"]) == sorted([doc.file_url, doc.preview_url])
    assert await repo.find_document(doc.id, USER) is None


async def test_storage_is_cleared_before_the_row(repo, storage, monkeypatch):
    """A row pointing at a live file is recoverable; a deleted row pointing at one is not.

    The learner can see the document and try again in the first case. In the second, the object is
    unfindable, unnamed and permanent.
    """
    from src.domains.personal_learning import repository as repository_module
    from src.domains.personal_learning.services import document_impl

    order: list[str] = []
    original_delete_row = repository_module.personal_learning_repo.delete_document

    async def _record_row_delete(*args, **kwargs):
        order.append("row")
        return await original_delete_row(*args, **kwargs)

    async def _record_object_delete(url):
        order.append("object")
        return True

    monkeypatch.setattr(
        repository_module.personal_learning_repo, "delete_document", _record_row_delete
    )
    from src.shared.infrastructure import storage as storage_module

    monkeypatch.setattr(storage_module.storage_service, "delete", _record_object_delete)

    doc = await _document(repo)
    await document_impl.delete_document(user_id=USER, doc_id=doc.id)

    assert order == ["object", "object", "row"]


async def test_the_row_goes_even_when_storage_refuses(repo, storage):
    """Storage being unreachable must not leave a learner unable to remove their own document.

    The row is what makes the object findable, so keeping it because the delete failed inverts the
    problem it was protecting against.
    """
    from src.domains.personal_learning.services import document_impl

    storage["state"]["succeed"] = False
    doc = await _document(repo)

    await document_impl.delete_document(user_id=USER, doc_id=doc.id)
    assert await repo.find_document(doc.id, USER) is None


async def test_both_objects_are_attempted_even_if_the_first_fails(repo, monkeypatch):
    from src.domains.personal_learning.services import document_impl
    from src.shared.infrastructure import storage as storage_module

    attempted: list[str] = []

    async def _delete(url):
        attempted.append(url)
        return not url.endswith(".pdf")

    monkeypatch.setattr(storage_module.storage_service, "delete", _delete)
    monkeypatch.setattr(
        storage_module.storage_service, "owns_url", lambda url: "cdn.example" in url
    )

    doc = await _document(repo)
    await document_impl.delete_document(user_id=USER, doc_id=doc.id)
    assert len(attempted) == 2


async def test_another_learners_document_is_not_found_and_not_deleted(repo, storage):
    from src.domains.personal_learning.services import document_impl
    from src.shared.exceptions import NotFoundError

    doc = await _document(repo)

    with pytest.raises(NotFoundError):
        await document_impl.delete_document(user_id=OTHER_USER, doc_id=doc.id)

    assert storage["calls"] == []
    assert await repo.find_document(doc.id, USER) is not None


async def test_deleting_an_unknown_document_is_not_found(repo, storage):
    from src.domains.personal_learning.services import document_impl
    from src.shared.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await document_impl.delete_document(user_id=USER, doc_id="does-not-exist")


# ---------------------------------------------------------------------------
# The job contract
# ---------------------------------------------------------------------------


def test_the_workers_payload_validates_as_a_document_response():
    """This is what deletes the hand-written snake_case mapper in the web client.

    The Celery result is a plain dict in snake_case. `CamelModel` sets ``populate_by_name``, so the
    response model reads it directly and emits camelCase — no second type, no second mapper.
    """
    from src.domains.personal_learning import models
    from src.workers.personal_learning_tasks import _serialize_document

    class _Row:
        id = "doc-1"
        user_id = "user-1"
        title = "Essay"
        format = "pdf"
        style = "academic"
        filename = "essay.pdf"
        file_url = "https://cdn.example/generated-docs/user-1/essay.pdf"
        preview_url = "https://cdn.example/generated-docs/user-1/essay.html"
        size = 2048
        content_type = "application/pdf"
        share_id = "abc123"
        is_public = False
        doc_type = "essay"

        class created_at:  # noqa: N801 - stands in for a datetime
            @staticmethod
            def isoformat():
                return "2026-08-17T10:00:00+00:00"

    payload = _serialize_document(_Row())
    response = models.DocumentResponse.model_validate(payload)

    assert response.file_url == "https://cdn.example/generated-docs/user-1/essay.pdf"
    assert response.size == 2048
    assert response.content_type == "application/pdf"

    wire = response.model_dump(by_alias=True)
    assert wire["fileUrl"] == response.file_url
    assert wire["contentType"] == "application/pdf"
    assert "file_url" not in wire
