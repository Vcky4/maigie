"""Note API, end to end.

Rewritten. What was here called `/api/v1/notes/...` — the domain is mounted at
`/api/v1/learning/notes` — used `PUT` where the API is `PATCH`, and exercised
`POST /notes/{id}/archive` and `/unarchive`, which have never existed in this codebase.
It survived by skipping: the first request 404'd, a `pytest.skip` on any non-201 swallowed
it, and the file reported as passing for as long as it has existed. A test that cannot
fail is worse than no test, because the row in the summary says otherwise.

Database-backed, so opt-in::

    RUN_DB_TESTS=1 DATABASE_URL=... pytest tests/test_notes.py
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

BASE = "/api/v1/learning/notes"


async def _create(client: AsyncClient, headers, **overrides) -> dict:
    body = {"title": "Test note", "content": "First draft.", "tags": ["testing"]}
    body.update(overrides)
    response = await client.post(BASE, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_note_lifecycle(client: AsyncClient, auth_headers):
    """Create, read, list, update, archive, delete — on the paths that exist."""
    note = await _create(client, auth_headers)
    note_id = note["id"]
    assert note["title"] == "Test note"
    assert note["archived"] is False
    assert [tag["tag"] for tag in note["tags"]] == ["testing"]

    fetched = await client.get(f"{BASE}/{note_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == note_id

    listed = await client.get(BASE, headers=auth_headers)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] >= 1
    # The canonical envelope, `pages` included.
    assert set(page) >= {"items", "total", "page", "pageSize", "pages"}
    assert any(item["id"] == note_id for item in page["items"])

    # Tags are replaced wholesale, and archiving is a field on the note rather than a verb.
    updated = await client.patch(
        f"{BASE}/{note_id}",
        json={"title": "Updated title", "tags": ["updated"]},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert [tag["tag"] for tag in updated.json()["tags"]] == ["updated"]
    assert updated.json()["content"] == "First draft."

    archived = await client.patch(
        f"{BASE}/{note_id}", json={"archived": True}, headers=auth_headers
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    # The default list excludes archived notes; asking for them returns it.
    default_list = await client.get(BASE, headers=auth_headers)
    assert all(item["id"] != note_id for item in default_list.json()["items"])
    archived_list = await client.get(f"{BASE}?archived=true", headers=auth_headers)
    assert any(item["id"] == note_id for item in archived_list.json()["items"])

    deleted = await client.delete(f"{BASE}/{note_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (await client.get(f"{BASE}/{note_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_tag_catalogue_counts_the_library(client: AsyncClient, auth_headers):
    """`GET /notes/tags`, which the filter chips need.

    Chips derived from a loaded page describe that page: a tag used only on older notes has no
    chip, and every count is a page count under a library heading.
    """
    first = await _create(client, auth_headers, title="One", tags=["algebra", "term-1"])
    second = await _create(client, auth_headers, title="Two", tags=["algebra"])

    response = await client.get(f"{BASE}/tags", headers=auth_headers)
    assert response.status_code == 200
    catalogue = {entry["tag"]: entry["count"] for entry in response.json()}
    assert catalogue["algebra"] == 2
    assert catalogue["term-1"] == 1

    await client.delete(f"{BASE}/{first['id']}", headers=auth_headers)
    await client.delete(f"{BASE}/{second['id']}", headers=auth_headers)


@pytest.mark.asyncio
async def test_editing_a_note_records_a_version_that_can_be_restored(
    client: AsyncClient, auth_headers
):
    """`NoteHistory` had no producer and no reader until migration 033."""
    note = await _create(client, auth_headers, content="what I wrote myself")
    note_id = note["id"]

    edited = await client.patch(
        f"{BASE}/{note_id}", json={"content": "something else"}, headers=auth_headers
    )
    assert edited.status_code == 200

    history = await client.get(f"{BASE}/{note_id}/history", headers=auth_headers)
    assert history.status_code == 200
    versions = history.json()["items"]
    assert len(versions) == 1
    assert versions[0]["content"] == "what I wrote myself"
    assert versions[0]["noteId"] == note_id

    restored = await client.post(
        f"{BASE}/{note_id}/history/{versions[0]['id']}/restore", headers=auth_headers
    )
    assert restored.status_code == 200
    assert restored.json()["content"] == "what I wrote myself"

    # Restoring is itself an overwrite, so the version it replaced is recorded too.
    after = await client.get(f"{BASE}/{note_id}/history", headers=auth_headers)
    assert after.json()["total"] == 2

    await client.delete(f"{BASE}/{note_id}", headers=auth_headers)


@pytest.mark.asyncio
async def test_history_of_another_learners_note_is_not_found(client: AsyncClient, auth_headers):
    response = await client.get(f"{BASE}/does-not-exist/history", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_note_endpoints_require_authentication(client: AsyncClient):
    for method, path in (
        ("get", BASE),
        ("get", f"{BASE}/tags"),
        ("get", f"{BASE}/any-id/history"),
    ):
        response = await getattr(client, method)(path)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


@pytest.mark.asyncio
async def test_note_course_association(client: AsyncClient, auth_headers):
    """A note can hang off a course, and the list can be filtered by it.

    The course is created at `/api/v1/knowledge/courses`. This test used to post to
    `/api/v1/courses`, which is not mounted, and skip when that failed.
    """
    course_response = await client.post(
        "/api/v1/knowledge/courses",
        json={
            "title": "Note Association Course",
            "description": "A course for testing notes",
            "difficulty": "BEGINNER",
        },
        headers=auth_headers,
    )
    assert course_response.status_code == 201, course_response.text
    course_id = course_response.json()["id"]

    note = await _create(client, auth_headers, title="Course note", courseId=course_id)
    assert note["courseId"] == course_id

    filtered = await client.get(f"{BASE}?courseId={course_id}", headers=auth_headers)
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["items"]] == [note["id"]]

    await client.delete(f"{BASE}/{note['id']}", headers=auth_headers)
    await client.delete(f"/api/v1/knowledge/courses/{course_id}", headers=auth_headers)
