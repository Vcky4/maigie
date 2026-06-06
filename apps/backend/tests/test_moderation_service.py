"""Unit tests for moderation_service.

Covers submit_report, decide_report, hide/restore circle, and ban_user_platform_wide.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_moderation_service.py -v``
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.moderation_service import (  # noqa: E402
    ModerationError,
    ban_user_platform_wide,
    decide_report,
    hide_circle,
    restore_circle,
    submit_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db():
    db = MagicMock()
    db.report = MagicMock()
    db.circle = MagicMock()
    db.circlemember = MagicMock()
    db.user = MagicMock()
    db.chatmessage = MagicMock()
    return db


# ---------------------------------------------------------------------------
# submit_report
# ---------------------------------------------------------------------------


class TestSubmitReport:
    @pytest.mark.asyncio
    async def test_creates_report_successfully(self):
        db = _mock_db()
        db.report.count = AsyncMock(return_value=0)  # no rate limit
        db.report.create = AsyncMock(return_value=SimpleNamespace(id="r-1", status="PENDING"))

        result = await submit_report(
            reporter_user_id="u1",
            target_type="CIRCLE",
            target_id="c1",
            reason_code="spam",
            description="This is spam",
            db_client=db,
        )
        assert result["reportId"] == "r-1"
        assert result["status"] == "PENDING"
        db.report.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limits_reports(self):
        db = _mock_db()
        db.report.count = AsyncMock(return_value=10)  # at limit

        with pytest.raises(ModerationError) as exc_info:
            await submit_report(
                reporter_user_id="u1",
                target_type="CIRCLE",
                target_id="c1",
                reason_code="spam",
                db_client=db,
            )
        assert exc_info.value.code == "REPORT_RATE_LIMITED"
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rejects_invalid_target_type(self):
        db = _mock_db()
        db.report.count = AsyncMock(return_value=0)

        with pytest.raises(ModerationError) as exc_info:
            await submit_report(
                reporter_user_id="u1",
                target_type="INVALID",
                target_id="x1",
                reason_code="spam",
                db_client=db,
            )
        assert exc_info.value.code == "INVALID_TARGET_TYPE"


# ---------------------------------------------------------------------------
# decide_report
# ---------------------------------------------------------------------------


class TestDecideReport:
    @pytest.mark.asyncio
    async def test_upheld_circle_hides_it(self):
        db = _mock_db()
        db.report.find_unique = AsyncMock(
            return_value=SimpleNamespace(
                id="r-1", targetType="CIRCLE", targetId="c1", status="PENDING"
            )
        )
        db.report.update = AsyncMock(return_value=None)
        db.circle.update = AsyncMock(return_value=None)

        result = await decide_report(
            report_id="r-1",
            decision="UPHELD",
            admin_user_id="admin-1",
            db_client=db,
        )
        assert "circle_hidden" in result["actionsTaken"]
        db.circle.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_upheld_profile_image_clears_url(self):
        db = _mock_db()
        db.report.find_unique = AsyncMock(
            return_value=SimpleNamespace(
                id="r-2", targetType="PROFILE_IMAGE", targetId="u1", status="PENDING"
            )
        )
        db.report.update = AsyncMock(return_value=None)
        db.user.update = AsyncMock(return_value=None)

        result = await decide_report(
            report_id="r-2",
            decision="UPHELD",
            admin_user_id="admin-1",
            db_client=db,
        )
        assert "profile_image_cleared" in result["actionsTaken"]

    @pytest.mark.asyncio
    async def test_dismissed_takes_no_action(self):
        db = _mock_db()
        db.report.find_unique = AsyncMock(
            return_value=SimpleNamespace(
                id="r-3", targetType="CIRCLE", targetId="c1", status="PENDING"
            )
        )
        db.report.update = AsyncMock(return_value=None)

        result = await decide_report(
            report_id="r-3",
            decision="DISMISSED",
            admin_user_id="admin-1",
            db_client=db,
        )
        assert result["actionsTaken"] == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_decision(self):
        db = _mock_db()
        with pytest.raises(ModerationError) as exc_info:
            await decide_report(
                report_id="r-1",
                decision="MAYBE",
                admin_user_id="admin-1",
                db_client=db,
            )
        assert exc_info.value.code == "INVALID_DECISION"

    @pytest.mark.asyncio
    async def test_rejects_not_found_report(self):
        db = _mock_db()
        db.report.find_unique = AsyncMock(return_value=None)

        with pytest.raises(ModerationError) as exc_info:
            await decide_report(
                report_id="r-999",
                decision="UPHELD",
                admin_user_id="admin-1",
                db_client=db,
            )
        assert exc_info.value.code == "REPORT_NOT_FOUND"


# ---------------------------------------------------------------------------
# hide_circle / restore_circle
# ---------------------------------------------------------------------------


class TestHideRestoreCircle:
    @pytest.mark.asyncio
    async def test_hide_sets_flag(self):
        db = _mock_db()
        db.circle.update = AsyncMock(return_value=None)
        await hide_circle("c1", db_client=db)
        db.circle.update.assert_called_once_with(
            where={"id": "c1"},
            data={"hiddenByModeration": True},
        )

    @pytest.mark.asyncio
    async def test_restore_clears_flag(self):
        db = _mock_db()
        db.circle.update = AsyncMock(return_value=None)
        await restore_circle("c1", db_client=db)
        db.circle.update.assert_called_once_with(
            where={"id": "c1"},
            data={"hiddenByModeration": False},
        )


# ---------------------------------------------------------------------------
# ban_user_platform_wide
# ---------------------------------------------------------------------------


class TestBanUserPlatformWide:
    @pytest.mark.asyncio
    @patch("src.services.seat_service.release_seat_on_member_remove", new_callable=AsyncMock)
    async def test_removes_from_all_circles_and_releases_seats(self, mock_release):
        db = _mock_db()
        db.circlemember.find_many = AsyncMock(
            return_value=[
                SimpleNamespace(id="m1", circleId="c1", userId="u1"),
                SimpleNamespace(id="m2", circleId="c2", userId="u1"),
            ]
        )
        mock_release.side_effect = [True, False]  # released 1 seat
        db.circlemember.delete = AsyncMock(return_value=None)
        db.user.update = AsyncMock(return_value=None)

        result = await ban_user_platform_wide("u1", db_client=db)
        assert len(result["circlesRemovedFrom"]) == 2
        assert result["seatsReleased"] == 1
        assert mock_release.call_count == 2
        db.user.update.assert_called_once()
