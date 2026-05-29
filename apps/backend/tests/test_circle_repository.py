"""Unit tests for circle_repository_service.

Covers list_public, list_featured, and get_public_detail.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_circle_repository.py -v``
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.circle_repository_service import (  # noqa: E402
    get_public_detail,
    list_featured,
    list_public,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db():
    db = MagicMock()
    db.circle = MagicMock()
    return db


def _circle(
    circle_id="c1",
    name="Test Circle",
    visibility="PUBLIC",
    hidden=False,
    plan_active=False,
    featured=False,
    members=None,
):
    return SimpleNamespace(
        id=circle_id,
        name=name,
        description="A test circle",
        category="Science",
        avatarUrl=None,
        bannerUrl=None,
        visibility=visibility,
        hiddenByModeration=hidden,
        circlePlanActive=plan_active,
        featured=featured,
        joinPolicy="AUTO_JOIN",
        createdAt="2025-01-01T00:00:00Z",
        members=members or [],
    )


# ---------------------------------------------------------------------------
# list_public
# ---------------------------------------------------------------------------


class TestListPublic:
    @pytest.mark.asyncio
    async def test_returns_public_non_hidden_circles(self):
        db = _mock_db()
        db.circle.count = AsyncMock(return_value=2)
        db.circle.find_many = AsyncMock(
            return_value=[
                _circle("c1", "Circle A", members=[SimpleNamespace(userId="u1")]),
                _circle(
                    "c2",
                    "Circle B",
                    members=[SimpleNamespace(userId="u1"), SimpleNamespace(userId="u2")],
                ),
            ]
        )

        result = await list_public(db_client=db)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        # Sorted by member count desc
        assert result["items"][0]["memberCount"] == 2
        assert result["items"][1]["memberCount"] == 1

    @pytest.mark.asyncio
    async def test_filters_by_category(self):
        db = _mock_db()
        db.circle.count = AsyncMock(return_value=1)
        db.circle.find_many = AsyncMock(return_value=[_circle("c1", "Math Circle")])

        result = await list_public(category="Math", db_client=db)
        # Verify the where clause includes category
        call_kwargs = db.circle.find_many.call_args[1]
        assert call_kwargs["where"]["category"] == "Math"

    @pytest.mark.asyncio
    async def test_search_query_filters(self):
        db = _mock_db()
        db.circle.count = AsyncMock(return_value=0)
        db.circle.find_many = AsyncMock(return_value=[])

        result = await list_public(query="biology", db_client=db)
        call_kwargs = db.circle.find_many.call_args[1]
        assert "OR" in call_kwargs["where"]


# ---------------------------------------------------------------------------
# list_featured
# ---------------------------------------------------------------------------


class TestListFeatured:
    @pytest.mark.asyncio
    async def test_returns_featured_circles(self):
        db = _mock_db()
        db.circle.find_many = AsyncMock(
            return_value=[
                _circle(
                    "c1",
                    "Featured Circle",
                    plan_active=True,
                    featured=True,
                    members=[SimpleNamespace(userId="u1")],
                ),
            ]
        )

        result = await list_featured(db_client=db)
        assert len(result) == 1
        assert result[0]["featured"] is True

    @pytest.mark.asyncio
    async def test_query_filters_for_featured(self):
        db = _mock_db()
        db.circle.find_many = AsyncMock(return_value=[])

        await list_featured(db_client=db)
        call_kwargs = db.circle.find_many.call_args[1]
        where = call_kwargs["where"]
        assert where["visibility"] == "PUBLIC"
        assert where["hiddenByModeration"] is False
        assert where["circlePlanActive"] is True
        assert where["featured"] is True


# ---------------------------------------------------------------------------
# get_public_detail
# ---------------------------------------------------------------------------


class TestGetPublicDetail:
    @pytest.mark.asyncio
    async def test_returns_detail_for_public_circle(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(
            return_value=_circle("c1", "Public Circle", members=[SimpleNamespace(userId="u1")])
        )

        result = await get_public_detail("c1", db_client=db)
        assert result is not None
        assert result["id"] == "c1"
        assert result["memberCount"] == 1

    @pytest.mark.asyncio
    async def test_returns_none_for_private_circle(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(return_value=_circle("c1", visibility="PRIVATE"))

        result = await get_public_detail("c1", db_client=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_hidden_circle(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(return_value=_circle("c1", hidden=True))

        result = await get_public_detail("c1", db_client=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_not_found(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(return_value=None)

        result = await get_public_detail("c-nonexistent", db_client=db)
        assert result is None
