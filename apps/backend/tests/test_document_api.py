"""Document routes over HTTP, against real Postgres.

The service-level behaviour is covered in ``test_document_lifecycle.py`` on SQLite. What can only
be checked here is the wire: the envelope, the camelCase contract, the status codes, who is refused,
and the route ordering that decides whether `/documents/formats` is read as a document id.

Rows are seeded through the repository rather than generated, because generating a document calls a
model and uploads two objects. The seeded URLs point at a host the storage client does not recognise
as ours, so deleting exercises the route without touching storage — provenance checking is covered
in ``test_storage.py``.

Opt-in::

    RUN_DB_TESTS=1 DATABASE_URL=... pytest tests/test_document_api.py
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/v1/learning/documents"


async def _me(client: AsyncClient, headers) -> str:
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _seed(user_id: str, *, title="Seeded essay", fmt="pdf", filename=None):
    """Insert a document row directly. Not our storage host, deliberately."""
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    marker = uuid.uuid4().hex[:8]
    return await repo.create_document(
        {
            "userId": user_id,
            "title": title,
            "format": fmt,
            "style": "academic",
            "filename": filename or f"seeded-{marker}.{fmt}",
            "fileUrl": f"https://not-our-cdn.invalid/seeded/{marker}.{fmt}",
            "previewUrl": f"https://not-our-cdn.invalid/seeded/{marker}.html",
            "size": 4096,
            "contentType": "application/pdf",
            "shareId": f"seed-{marker}",
            "isPublic": False,
        }
    )


@pytest.mark.asyncio
async def test_document_endpoints_require_authentication(client: AsyncClient):
    for method, path in (
        ("get", BASE),
        ("get", f"{BASE}/formats"),
        ("get", f"{BASE}/any-id"),
        ("delete", f"{BASE}/any-id"),
        ("post", f"{BASE}/any-id/unpublish"),
    ):
        response = await getattr(client, method)(path)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


@pytest.mark.asyncio
async def test_the_list_uses_the_canonical_envelope_and_camelcase(
    client: AsyncClient, auth_headers
):
    """`DocumentListResponse` was a third pagination envelope with no `pages`."""
    user_id = await _me(client, auth_headers)
    doc = await _seed(user_id)

    response = await client.get(BASE, headers=auth_headers)
    assert response.status_code == 200
    page = response.json()
    assert set(page) >= {"items", "total", "page", "pageSize", "pages"}

    item = next(entry for entry in page["items"] if entry["id"] == doc.id)
    # Every one of these is NOT NULL in the table, so none of them is optional on the wire.
    for field in ("fileUrl", "previewUrl", "contentType", "shareId", "size", "isPublic"):
        assert field in item, field
    assert item["size"] == 4096
    assert "file_url" not in item

    await client.delete(f"{BASE}/{doc.id}", headers=auth_headers)


@pytest.mark.asyncio
async def test_search_and_format_filter_in_the_query(client: AsyncClient, auth_headers):
    user_id = await _me(client, auth_headers)
    marker = uuid.uuid4().hex[:6]
    wanted = await _seed(user_id, title=f"Mitochondria {marker}", fmt="docx")
    other = await _seed(user_id, title=f"Photosynthesis {marker}", fmt="pdf")

    by_search = await client.get(f"{BASE}?search=Mitochondria%20{marker}", headers=auth_headers)
    assert by_search.status_code == 200
    assert [entry["id"] for entry in by_search.json()["items"]] == [wanted.id]
    assert by_search.json()["total"] == 1

    by_format = await client.get(f"{BASE}?search={marker}&format=pdf", headers=auth_headers)
    assert [entry["id"] for entry in by_format.json()["items"]] == [other.id]

    for doc_id in (wanted.id, other.id):
        await client.delete(f"{BASE}/{doc_id}", headers=auth_headers)


@pytest.mark.asyncio
async def test_formats_is_not_read_as_a_document_id(client: AsyncClient, auth_headers):
    """Declaration order decides this, and getting it wrong makes the route unreachable."""
    user_id = await _me(client, auth_headers)
    doc = await _seed(user_id, fmt="pptx")

    response = await client.get(f"{BASE}/formats", headers=auth_headers)
    assert response.status_code == 200
    counts = {entry["format"]: entry["count"] for entry in response.json()}
    assert counts.get("pptx", 0) >= 1

    await client.delete(f"{BASE}/{doc.id}", headers=auth_headers)


@pytest.mark.asyncio
async def test_publish_then_unpublish_retires_the_link(client: AsyncClient, auth_headers):
    user_id = await _me(client, auth_headers)
    doc = await _seed(user_id)

    published = await client.post(f"{BASE}/{doc.id}/publish", headers=auth_headers)
    assert published.status_code == 200
    share_id = published.json()["shareId"]
    assert published.json()["isPublic"] is True

    # Public documents resolve for anyone, so no auth header here.
    shared = await client.get(f"{BASE}/share/{share_id}")
    assert shared.status_code == 200
    assert shared.json()["id"] == doc.id

    withdrawn = await client.post(f"{BASE}/{doc.id}/unpublish", headers=auth_headers)
    assert withdrawn.status_code == 200
    assert withdrawn.json()["isPublic"] is False
    assert withdrawn.json()["shareId"] != share_id

    assert (await client.get(f"{BASE}/share/{share_id}")).status_code == 404

    await client.delete(f"{BASE}/{doc.id}", headers=auth_headers)


@pytest.mark.asyncio
async def test_delete_returns_204_and_the_document_is_gone(client: AsyncClient, auth_headers):
    user_id = await _me(client, auth_headers)
    doc = await _seed(user_id)

    response = await client.delete(f"{BASE}/{doc.id}", headers=auth_headers)
    assert response.status_code == 204
    assert not response.content

    assert (await client.get(f"{BASE}/{doc.id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_deleting_an_unknown_document_is_404(client: AsyncClient, auth_headers):
    response = await client.delete(f"{BASE}/{uuid.uuid4().hex}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unsupported_format_is_refused_before_the_job_is_queued(
    client: AsyncClient, auth_headers
):
    """The async route used to accept anything and fail inside the worker.

    The learner then waited for a job that could never succeed, instead of being told immediately.
    """
    response = await client.post(
        f"{BASE}/async",
        json={"type": "essay", "title": "A", "prompt": "B", "format": "epub"},
        headers=auth_headers,
    )
    assert response.status_code == 400
