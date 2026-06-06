"""Unit tests for circle_billing_service.

Covers purchase_circle_plan, cancel_circle_plan, purchase_seat_addon,
cancel_seat_addon, and handle_circle_billing_webhook.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_circle_billing.py -v``
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.circle_billing_service import (  # noqa: E402
    CIRCLE_PLAN_ALREADY_ACTIVE,
    CIRCLE_PLAN_NOT_ACTIVE,
    PAYMENT_METHOD_REQUIRED,
    SEAT_MANAGEMENT_FORBIDDEN,
    CircleBillingError,
    cancel_circle_plan,
    cancel_seat_addon,
    handle_circle_billing_webhook,
    purchase_circle_plan,
    purchase_seat_addon,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db():
    db = MagicMock()
    db.circlemember = MagicMock()
    db.circle = MagicMock()
    db.circlesubscription = MagicMock()
    db.circleseataddon = MagicMock()
    return db


def _member(user_id="u1", role="OWNER", user=None):
    m = SimpleNamespace(
        id=f"m-{user_id}",
        userId=user_id,
        circleId="c1",
        role=role,
        user=user
        or SimpleNamespace(
            id=user_id,
            stripeCustomerId="cus_test123",
            paystackCustomerCode=None,
        ),
    )
    return m


def _circle(plan_active=False, pool_size=0):
    return SimpleNamespace(
        id="c1",
        circlePlanActive=plan_active,
        seatPoolSize=pool_size,
        circlePlanCurrentPeriodEnd=None,
    )


# ---------------------------------------------------------------------------
# purchase_circle_plan
# ---------------------------------------------------------------------------


class TestPurchaseCirclePlan:
    @pytest.mark.asyncio
    async def test_rejects_non_owner_non_admin(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="MEMBER"))

        with pytest.raises(CircleBillingError) as exc_info:
            await purchase_circle_plan("u1", "c1", db_client=db)
        assert exc_info.value.code == SEAT_MANAGEMENT_FORBIDDEN

    @pytest.mark.asyncio
    async def test_rejects_already_active_plan(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=True))

        with pytest.raises(CircleBillingError) as exc_info:
            await purchase_circle_plan("u1", "c1", db_client=db)
        assert exc_info.value.code == CIRCLE_PLAN_ALREADY_ACTIVE

    @pytest.mark.asyncio
    async def test_rejects_no_payment_method(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=False))
        db.circlemember.find_first = AsyncMock(
            return_value=_member(
                user=SimpleNamespace(
                    id="u1",
                    stripeCustomerId=None,
                    paystackCustomerCode=None,
                )
            )
        )

        with pytest.raises(CircleBillingError) as exc_info:
            await purchase_circle_plan("u1", "c1", db_client=db)
        assert exc_info.value.code == PAYMENT_METHOD_REQUIRED
        assert exc_info.value.status_code == 402

    @pytest.mark.asyncio
    @patch("src.services.seat_service.activate_circle_plan_seats", new_callable=AsyncMock)
    async def test_successful_purchase(self, mock_activate):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=False))
        db.circlemember.find_first = AsyncMock(return_value=_member())
        db.circlesubscription.find_first = AsyncMock(return_value=None)  # first purchase
        db.circlesubscription.create = AsyncMock(
            return_value=SimpleNamespace(id="sub-1", status="TRIALING")
        )
        db.circle.update = AsyncMock(return_value=None)
        mock_activate.return_value = None

        result = await purchase_circle_plan("u1", "c1", db_client=db)
        assert result["circlePlanActive"] is True
        assert result["circleId"] == "c1"
        mock_activate.assert_called_once()


# ---------------------------------------------------------------------------
# cancel_circle_plan
# ---------------------------------------------------------------------------


class TestCancelCirclePlan:
    @pytest.mark.asyncio
    async def test_rejects_when_no_active_plan(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=False))

        with pytest.raises(CircleBillingError) as exc_info:
            await cancel_circle_plan("u1", "c1", db_client=db)
        assert exc_info.value.code == CIRCLE_PLAN_NOT_ACTIVE

    @pytest.mark.asyncio
    async def test_successful_cancel(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle(plan_active=True))
        db.circlesubscription.find_first = AsyncMock(
            return_value=SimpleNamespace(id="sub-1", status="ACTIVE")
        )
        db.circlesubscription.update = AsyncMock(return_value=None)

        result = await cancel_circle_plan("u1", "c1", db_client=db)
        assert result["status"] == "CANCELED"


# ---------------------------------------------------------------------------
# purchase_seat_addon
# ---------------------------------------------------------------------------


class TestPurchaseSeatAddon:
    @pytest.mark.asyncio
    async def test_successful_addon_purchase(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="ADMIN"))
        db.circle.find_unique = AsyncMock(return_value=_circle(pool_size=4))
        db.circlemember.find_first = AsyncMock(return_value=_member())
        db.circleseataddon.create = AsyncMock(return_value=SimpleNamespace(id="addon-1"))
        db.circle.update = AsyncMock(return_value=None)

        result = await purchase_seat_addon("u1", "c1", quantity=2, db_client=db)
        assert result["quantity"] == 2
        assert result["seatPoolSize"] == 6  # 4 + 2

    @pytest.mark.asyncio
    async def test_rejects_invalid_quantity(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle())

        with pytest.raises(CircleBillingError) as exc_info:
            await purchase_seat_addon("u1", "c1", quantity=0, db_client=db)
        assert exc_info.value.code == "INVALID_QUANTITY"


# ---------------------------------------------------------------------------
# cancel_seat_addon
# ---------------------------------------------------------------------------


class TestCancelSeatAddon:
    @pytest.mark.asyncio
    async def test_successful_cancel(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle())
        db.circleseataddon.find_unique = AsyncMock(
            return_value=SimpleNamespace(
                id="addon-1", circleId="c1", status="ACTIVE", currentPeriodEnd=None
            )
        )
        db.circleseataddon.update = AsyncMock(return_value=None)

        result = await cancel_seat_addon("u1", "c1", "addon-1", db_client=db)
        assert result["status"] == "CANCELED_AT_PERIOD_END"

    @pytest.mark.asyncio
    async def test_rejects_already_canceled(self):
        db = _mock_db()
        db.circlemember.find_unique = AsyncMock(return_value=SimpleNamespace(role="OWNER"))
        db.circle.find_unique = AsyncMock(return_value=_circle())
        db.circleseataddon.find_unique = AsyncMock(
            return_value=SimpleNamespace(
                id="addon-1", circleId="c1", status="CANCELED", currentPeriodEnd=None
            )
        )

        with pytest.raises(CircleBillingError) as exc_info:
            await cancel_seat_addon("u1", "c1", "addon-1", db_client=db)
        assert exc_info.value.code == "ADDON_ALREADY_CANCELED"


# ---------------------------------------------------------------------------
# handle_circle_billing_webhook
# ---------------------------------------------------------------------------


class TestHandleCircleBillingWebhook:
    @pytest.mark.asyncio
    async def test_no_op_for_unknown_event(self):
        db = _mock_db()
        result = await handle_circle_billing_webhook("unknown.event", {}, db_client=db)
        assert result["action"] == "no_op"

    @pytest.mark.asyncio
    async def test_invoice_paid_renews_subscription(self):
        db = _mock_db()
        db.circlesubscription.find_first = AsyncMock(
            return_value=SimpleNamespace(id="sub-1", circleId="c1")
        )
        db.circlesubscription.update = AsyncMock(return_value=None)
        db.circle.update = AsyncMock(return_value=None)

        result = await handle_circle_billing_webhook(
            "invoice.paid",
            {"subscription": "ext-sub-123"},
            db_client=db,
        )
        assert result["action"] == "renewed"
        assert result["circleId"] == "c1"

    @pytest.mark.asyncio
    async def test_payment_failed_marks_past_due(self):
        db = _mock_db()
        db.circlesubscription.find_first = AsyncMock(
            return_value=SimpleNamespace(id="sub-1", circleId="c1")
        )
        db.circlesubscription.update = AsyncMock(return_value=None)

        result = await handle_circle_billing_webhook(
            "invoice.payment_failed",
            {"subscription": "ext-sub-123"},
            db_client=db,
        )
        assert result["action"] == "marked_past_due"
