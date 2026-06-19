"""Unit tests for the Usage_Scope additions to ``usage_tracking_service``.

Covers ``emit_ai_usage``, ``get_personal_usage_summary``, and
``get_circle_usage_summary`` for Requirements 7.5, 7.6, 12.2, 13.2, 13.3.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_usage_tracking_scope.py``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Skip the global DB fixture for these pure unit tests.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.usage_tracking_service import (  # noqa: E402
    PERSONAL_USAGE_SCOPE,
    build_circle_usage_scope,
    emit_ai_usage,
    get_circle_usage_summary,
    get_personal_usage_summary,
)


# ---------------------------------------------------------------------------
# build_circle_usage_scope
# ---------------------------------------------------------------------------


class TestBuildCircleUsageScope:
    def test_builds_canonical_circle_scope(self) -> None:
        assert build_circle_usage_scope("circle-123") == "circle:circle-123"

    def test_rejects_empty_circle_id(self) -> None:
        with pytest.raises(ValueError):
            build_circle_usage_scope("")


# ---------------------------------------------------------------------------
# emit_ai_usage
# ---------------------------------------------------------------------------


def _make_mock_db() -> MagicMock:
    """Build a Prisma-like mock that records ``aiusagerecord.create`` calls."""
    aiusagerecord = MagicMock()
    aiusagerecord.create = AsyncMock(return_value=None)
    aiusagerecord.find_many = AsyncMock(return_value=[])
    db = MagicMock()
    db.aiusagerecord = aiusagerecord
    return db


class TestEmitAiUsage:
    @pytest.mark.asyncio
    async def test_personal_scope_persists_with_null_circle_id(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-1",
            usage_scope=PERSONAL_USAGE_SCOPE,
            circle_id=None,
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat_default",
            input_tokens=100,
            output_tokens=42,
            request_count=1,
            db_client=db,
        )
        db.aiusagerecord.create.assert_awaited_once()
        kwargs = db.aiusagerecord.create.await_args.kwargs
        data = kwargs["data"]
        assert data["userId"] == "u-1"
        assert data["usageScope"] == "personal"
        assert data["circleId"] is None
        assert data["provider"] == "gemini"
        assert data["model"] == "gemini-3.5-flash"
        assert data["feature"] == "chat_default"
        assert data["inputTokens"] == 100
        assert data["outputTokens"] == 42
        assert data["requestCount"] == 1

    @pytest.mark.asyncio
    async def test_circle_scope_persists_with_matching_circle_id(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-2",
            usage_scope="circle:c-99",
            circle_id="c-99",
            provider="openai",
            model="gpt-4o-mini",
            feature="chat_tools_session",
            input_tokens=10,
            output_tokens=20,
            db_client=db,
        )
        db.aiusagerecord.create.assert_awaited_once()
        data = db.aiusagerecord.create.await_args.kwargs["data"]
        assert data["usageScope"] == "circle:c-99"
        assert data["circleId"] == "c-99"

    @pytest.mark.asyncio
    async def test_rejects_personal_scope_with_circle_id(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-3",
            usage_scope=PERSONAL_USAGE_SCOPE,
            circle_id="c-1",
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            db_client=db,
        )
        # Validation should reject the call before any DB write happens.
        db.aiusagerecord.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_circle_scope_with_mismatched_circle_id(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-4",
            usage_scope="circle:c-99",
            circle_id="c-other",
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            db_client=db,
        )
        db.aiusagerecord.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_circle_scope_without_circle_id(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-5",
            usage_scope="circle:c-1",
            circle_id=None,
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            db_client=db,
        )
        db.aiusagerecord.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unknown_scope_kind(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-6",
            usage_scope="squad:s-1",
            circle_id=None,
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            db_client=db,
        )
        db.aiusagerecord.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clamps_negative_token_counts(self) -> None:
        db = _make_mock_db()
        await emit_ai_usage(
            user_id="u-7",
            usage_scope=PERSONAL_USAGE_SCOPE,
            circle_id=None,
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            input_tokens=-10,
            output_tokens=-1,
            request_count=0,
            db_client=db,
        )
        data = db.aiusagerecord.create.await_args.kwargs["data"]
        assert data["inputTokens"] == 0
        assert data["outputTokens"] == 0
        assert data["requestCount"] == 1

    @pytest.mark.asyncio
    async def test_db_failure_is_swallowed(self) -> None:
        db = _make_mock_db()
        db.aiusagerecord.create = AsyncMock(side_effect=RuntimeError("boom"))
        # Must not raise; telemetry failures must never break AI calls.
        await emit_ai_usage(
            user_id="u-8",
            usage_scope=PERSONAL_USAGE_SCOPE,
            circle_id=None,
            provider="gemini",
            model="gemini-3.5-flash",
            feature="chat",
            db_client=db,
        )


# ---------------------------------------------------------------------------
# Personal vs Circle summary filtering (Property 8 sanity)
# ---------------------------------------------------------------------------


def _record(
    *, request_count: int = 1, input_tokens: int = 0, output_tokens: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        requestCount=request_count,
        inputTokens=input_tokens,
        outputTokens=output_tokens,
    )


class TestPersonalUsageSummary:
    @pytest.mark.asyncio
    async def test_filters_to_personal_scope_only(self) -> None:
        db = _make_mock_db()
        db.aiusagerecord.find_many = AsyncMock(
            return_value=[
                _record(request_count=1, input_tokens=10, output_tokens=20),
                _record(request_count=2, input_tokens=5, output_tokens=7),
            ]
        )
        result = await get_personal_usage_summary("u-1", db_client=db)
        # Where filter scopes to userId + usageScope='personal'.
        call_kwargs = db.aiusagerecord.find_many.await_args.kwargs
        assert call_kwargs["where"]["userId"] == "u-1"
        assert call_kwargs["where"]["usageScope"] == PERSONAL_USAGE_SCOPE
        # Aggregates correctly.
        assert result["request_count"] == 3
        assert result["input_tokens"] == 15
        assert result["output_tokens"] == 27
        assert result["scope"] == "personal"


class TestCircleUsageSummary:
    @pytest.mark.asyncio
    async def test_filters_to_specific_circle_scope(self) -> None:
        db = _make_mock_db()
        db.aiusagerecord.find_many = AsyncMock(
            return_value=[
                _record(request_count=4, input_tokens=8, output_tokens=12),
            ]
        )
        result = await get_circle_usage_summary("c-1", db_client=db)
        call_kwargs = db.aiusagerecord.find_many.await_args.kwargs
        assert call_kwargs["where"]["usageScope"] == "circle:c-1"
        assert call_kwargs["where"]["circleId"] == "c-1"
        assert "userId" not in call_kwargs["where"]
        assert result["request_count"] == 4
        assert result["scope"] == "circle:c-1"
        assert result["circle_id"] == "c-1"

    @pytest.mark.asyncio
    async def test_filters_to_specific_member_when_user_id_given(self) -> None:
        db = _make_mock_db()
        db.aiusagerecord.find_many = AsyncMock(return_value=[])
        await get_circle_usage_summary("c-1", user_id="u-7", db_client=db)
        where = db.aiusagerecord.find_many.await_args.kwargs["where"]
        assert where["userId"] == "u-7"
        assert where["circleId"] == "c-1"
        assert where["usageScope"] == "circle:c-1"
