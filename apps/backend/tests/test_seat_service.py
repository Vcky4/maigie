"""Unit tests for seat_service.

Covers get_seat_tier, list_seats, assign_seat, unassign_seat, reassign_seat,
release_seat_on_member_remove, and reconcile_seat_pool_on_addon_change.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_seat_service.py -v``
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.seat_service import (  # noqa: E402
    INSUFFICIENT_SEATS,
    SEAT_MANAGEMENT_FORBIDDEN,
    TARGET_ALREADY_HAS_PLUS_SEAT,
    TARGET_DOES_NOT_HAVE_PLUS_SEAT,
    TARGET_NOT_MEMBER,
    SeatServiceError,
    assign_seat,
    get_seat_tier,
    list_seats,
    reassign_seat,
    reconcile_seat_pool_on_addon_change,
    release_seat_on_member_remove,
    unassign_seat,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db():
    db = MagicMock()
    db.circlemember = MagicMock()
    db.circle = MagicMock()
    db.circleseataddon = MagicMock()
    return db


def _member(user_id="u1", circle_id="c1", role="MEMBER", seat_tier="FREE_SEAT"):
    return SimpleNamespace(
        id=f"m-{user_id}",
        userId=user_id,
        circleId=circle_id,
        role=role,
        seatTier=seat_tier,
        joinedAt="2025-01-01T00:00:00Z",
    )


def _circle(circle_id="c1", plan_active=False, pool_size=0):
    return SimpleNamespace(
        id=circle_id,
        circlePlanActive=plan_active,
        seatPoolSize=pool_size,
    )


# ---------------------------------------------------------------------------
# get_seat_tier
# ---------------------------------------------------------------------------


class TestGetSeatTier:
    @pytest.mark.asyncio
    async def test_returns_seat_tier_for_member(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(seat_tier="PLUS_SEAT"))
        result = await get_seat_tier("u1", "c1", db_client=db)
        assert result == "PLUS_SEAT"

    @pytest.mark.asyncio
    async def test_returns_free_seat_for_non_member(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=None)
        result = await get_seat_tier("u1", "c1", db_client=db)
        assert result == "FREE_SEAT"

    @pytest.mark.asyncio
    async def test_returns_free_seat_on_exception(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(side_effect=RuntimeError("boom"))
        result = await get_seat_tier("u1", "c1", db_client=db)
        assert result == "FREE_SEAT"


# ---------------------------------------------------------------------------
# list_seats
# ---------------------------------------------------------------------------


class TestListSeats:
    @pytest.mark.asyncio
    async def test_returns_seats_for_owner(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(pool_size=4, plan_active=True))
        db.circlemember.find_many = AsyncMock(
            return_value=[_member(user_id="u1", seat_tier="PLUS_SEAT")]
        )
        # Mock user relation
        db.circlemember.find_many.return_value[0].user = SimpleNamespace(name="Test User")

        result = await list_seats("c1", "u1", db_client=db)
        assert result["seatPoolSize"] == 4
        assert result["circlePlanActive"] is True
        assert len(result["seats"]) == 1

    @pytest.mark.asyncio
    async def test_rejects_non_owner_non_admin(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(role="MEMBER"))

        with pytest.raises(SeatServiceError) as exc_info:
            await list_seats("c1", "u1", db_client=db)
        assert exc_info.value.code == SEAT_MANAGEMENT_FORBIDDEN
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rejects_non_member(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=None)

        with pytest.raises(SeatServiceError) as exc_info:
            await list_seats("c1", "u1", db_client=db)
        assert exc_info.value.code == SEAT_MANAGEMENT_FORBIDDEN


# ---------------------------------------------------------------------------
# assign_seat
# ---------------------------------------------------------------------------


class TestAssignSeat:
    @pytest.mark.asyncio
    async def test_assigns_seat_successfully(self):
        db = _mock_db()
        # Actor is OWNER
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(user_id="owner", role="OWNER"),  # actor check
                _member(user_id="target", seat_tier="FREE_SEAT"),  # target check
            ]
        )
        db.circle.find_unique = AsyncMock(return_value=_circle(pool_size=4))
        db.circlemember.count = AsyncMock(return_value=1)  # 1 assigned < 4 pool
        db.circlemember.update = AsyncMock(
            return_value=SimpleNamespace(
                userId="target", circleId="c1", seatTier="PLUS_SEAT", role="MEMBER"
            )
        )

        result = await assign_seat("c1", "target", "owner", db_client=db)
        assert result["seatTier"] == "PLUS_SEAT"
        db.circlemember.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_non_admin_actor(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(role="MEMBER"))

        with pytest.raises(SeatServiceError) as exc_info:
            await assign_seat("c1", "target", "actor", db_client=db)
        assert exc_info.value.code == SEAT_MANAGEMENT_FORBIDDEN

    @pytest.mark.asyncio
    async def test_rejects_non_member_target(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),  # actor OK
                None,  # target not found
            ]
        )

        with pytest.raises(SeatServiceError) as exc_info:
            await assign_seat("c1", "target", "owner", db_client=db)
        assert exc_info.value.code == TARGET_NOT_MEMBER

    @pytest.mark.asyncio
    async def test_rejects_already_plus_seat(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),
                _member(user_id="target", seat_tier="PLUS_SEAT"),
            ]
        )

        with pytest.raises(SeatServiceError) as exc_info:
            await assign_seat("c1", "target", "owner", db_client=db)
        assert exc_info.value.code == TARGET_ALREADY_HAS_PLUS_SEAT

    @pytest.mark.asyncio
    async def test_rejects_insufficient_seats(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),
                _member(user_id="target", seat_tier="FREE_SEAT"),
            ]
        )
        db.circle.find_unique = AsyncMock(return_value=_circle(pool_size=2))
        db.circlemember.count = AsyncMock(return_value=2)  # 2 assigned == 2 pool

        with pytest.raises(SeatServiceError) as exc_info:
            await assign_seat("c1", "target", "owner", db_client=db)
        assert exc_info.value.code == INSUFFICIENT_SEATS


# ---------------------------------------------------------------------------
# unassign_seat
# ---------------------------------------------------------------------------


class TestUnassignSeat:
    @pytest.mark.asyncio
    async def test_unassigns_seat_successfully(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="ADMIN"),  # actor
                _member(user_id="target", seat_tier="PLUS_SEAT"),  # target
            ]
        )
        db.circlemember.update = AsyncMock(
            return_value=SimpleNamespace(
                userId="target", circleId="c1", seatTier="FREE_SEAT", role="MEMBER"
            )
        )

        result = await unassign_seat("c1", "target", "admin", db_client=db)
        assert result["seatTier"] == "FREE_SEAT"

    @pytest.mark.asyncio
    async def test_rejects_target_without_plus_seat(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),
                _member(user_id="target", seat_tier="FREE_SEAT"),
            ]
        )

        with pytest.raises(SeatServiceError) as exc_info:
            await unassign_seat("c1", "target", "owner", db_client=db)
        assert exc_info.value.code == TARGET_DOES_NOT_HAVE_PLUS_SEAT


# ---------------------------------------------------------------------------
# reassign_seat
# ---------------------------------------------------------------------------


class TestReassignSeat:
    @pytest.mark.asyncio
    async def test_reassigns_atomically(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),  # actor
                _member(user_id="from_user", seat_tier="PLUS_SEAT"),  # source
                _member(user_id="to_user", seat_tier="FREE_SEAT"),  # dest
            ]
        )

        # Mock transaction context manager
        tx_mock = MagicMock()
        tx_mock.circlemember = MagicMock()
        tx_mock.circlemember.update = AsyncMock(
            side_effect=[
                SimpleNamespace(userId="from_user", circleId="c1", seatTier="FREE_SEAT"),
                SimpleNamespace(userId="to_user", circleId="c1", seatTier="PLUS_SEAT"),
            ]
        )

        class FakeTx:
            async def __aenter__(self):
                return tx_mock

            async def __aexit__(self, *args):
                pass

        db.tx = FakeTx

        result = await reassign_seat("c1", "from_user", "to_user", "owner", db_client=db)
        assert result["from"]["seatTier"] == "FREE_SEAT"
        assert result["to"]["seatTier"] == "PLUS_SEAT"

    @pytest.mark.asyncio
    async def test_rejects_source_without_plus_seat(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(
            side_effect=[
                _member(role="OWNER"),
                _member(user_id="from_user", seat_tier="FREE_SEAT"),  # no seat
            ]
        )

        with pytest.raises(SeatServiceError) as exc_info:
            await reassign_seat("c1", "from_user", "to_user", "owner", db_client=db)
        assert exc_info.value.code == TARGET_DOES_NOT_HAVE_PLUS_SEAT


# ---------------------------------------------------------------------------
# release_seat_on_member_remove
# ---------------------------------------------------------------------------


class TestReleaseSeatOnMemberRemove:
    @pytest.mark.asyncio
    async def test_releases_plus_seat(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(seat_tier="PLUS_SEAT"))
        db.circlemember.update = AsyncMock(return_value=None)

        result = await release_seat_on_member_remove("c1", "u1", db_client=db)
        assert result is True
        db.circlemember.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_for_free_seat(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=_member(seat_tier="FREE_SEAT"))

        result = await release_seat_on_member_remove("c1", "u1", db_client=db)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_non_member(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=None)

        result = await release_seat_on_member_remove("c1", "u1", db_client=db)
        assert result is False


# ---------------------------------------------------------------------------
# reconcile_seat_pool_on_addon_change
# ---------------------------------------------------------------------------


class TestReconcileSeatPool:
    @pytest.mark.asyncio
    async def test_trims_excess_seats(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=False, pool_size=2))
        db.circleseataddon.count = AsyncMock(return_value=1)  # 1 active addon
        # 3 members have PLUS_SEAT but new pool is only 1
        db.circlemember.find_many = AsyncMock(
            return_value=[
                _member(user_id="u1", role="MEMBER", seat_tier="PLUS_SEAT"),
                _member(user_id="u2", role="MEMBER", seat_tier="PLUS_SEAT"),
                _member(user_id="u3", role="MEMBER", seat_tier="PLUS_SEAT"),
            ]
        )
        db.circlemember.update = AsyncMock(return_value=None)
        db.circle.update = AsyncMock(return_value=None)

        result = await reconcile_seat_pool_on_addon_change("c1", db_client=db)
        assert result["newSeatPoolSize"] == 1  # 0 plan + 1 addon
        assert len(result["unassignedUsers"]) == 2  # 3 assigned - 1 pool = 2 excess

    @pytest.mark.asyncio
    async def test_includes_plan_seats_when_active(self):
        db = _mock_db()
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=True, pool_size=6))
        db.circleseataddon.count = AsyncMock(return_value=2)  # 2 active addons
        db.circlemember.find_many = AsyncMock(return_value=[])  # no assigned seats
        db.circle.update = AsyncMock(return_value=None)

        result = await reconcile_seat_pool_on_addon_change("c1", db_client=db)
        assert result["newSeatPoolSize"] == 6  # 4 plan + 2 addon
