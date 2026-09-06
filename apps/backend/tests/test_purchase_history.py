"""Purchase history reads `PlusPurchase`, and the credit tables are gone. Decision H.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision H. `GET /billing/purchases` used to read
`CreditPurchaseTransaction` joined to `CreditPack` and shape a credit-pack line — `packName`,
`credits`, `amountPaid`. Credit packs are the withdrawn product and nobody ever bought one (Phase 2b's
count found zero completed rows), so both tables are dropped and the endpoint reads the passes and
subscriptions a learner actually bought.

The behaviour that has to survive the change is the one the endpoint exists for: it is the support
surface that answers "what did I pay you", so it must list every purchase whatever its status.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_purchase_history.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.billing.services import credit_purchase_service  # noqa: E402


def a_purchase(**overrides):
    """A `PlusPurchase`-shaped row, carrying only what the service reads off it."""
    row = {
        "id": "pur_1",
        "product_id": "plus_pass_5h",
        "product_kind": "pass",
        "provider": "stripe",
        "amount_minor": 99,
        "currency": "USD",
        "status": "completed",
        "completed_at": datetime(2026, 9, 1, tzinfo=UTC),
        "refunded_at": None,
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    row.update(overrides)
    return SimpleNamespace(**row)


@pytest.fixture
def history(monkeypatch):
    """Stub the repository so the service is tested without a database.

    Returns a setter: a test hands it the rows the repository should yield, and reads back what the
    service turned them into.
    """
    state: dict = {"rows": [], "total": 0}

    async def fake_repo_history(user_id, *, skip=0, take=20):
        return state["rows"], state["total"]

    monkeypatch.setattr(
        credit_purchase_service.billing_repo, "get_purchase_history", fake_repo_history
    )
    return state


class TestTheHistoryReadsPurchases:
    @pytest.mark.asyncio
    async def test_a_purchase_becomes_an_item_in_the_new_shape(self, history):
        history["rows"] = [a_purchase()]
        history["total"] = 1

        result = await credit_purchase_service.get_purchase_history("u1")
        item = result["items"][0]

        # The shape describes what was bought, not a credit pack.
        assert item["productId"] == "plus_pass_5h"
        assert item["productKind"] == "pass"
        assert item["provider"] == "stripe"
        assert item["amountMinor"] == 99
        # None of the credit-pack fields survive.
        assert "packName" not in item
        assert "creditsGranted" not in item
        assert "creditPackId" not in item

    @pytest.mark.asyncio
    async def test_a_subscription_purchase_is_labelled_as_one(self, history):
        """A pass and the subscription are different lines on a receipt even at the same price, which
        is why `productKind` is published rather than inferred from the amount.
        """
        history["rows"] = [
            a_purchase(id="pur_2", product_id="plus_monthly", product_kind="subscription")
        ]
        history["total"] = 1
        result = await credit_purchase_service.get_purchase_history("u1")
        assert result["items"][0]["productKind"] == "subscription"

    @pytest.mark.asyncio
    async def test_a_refunded_purchase_is_listed_not_hidden(self, history):
        """The endpoint answers "what did I pay you", so a refund is part of the answer. Hiding
        non-completed rows is how a support tool becomes an argument.
        """
        history["rows"] = [
            a_purchase(status="refunded", refunded_at=datetime(2026, 9, 3, tzinfo=UTC))
        ]
        history["total"] = 1
        result = await credit_purchase_service.get_purchase_history("u1")
        item = result["items"][0]
        assert item["status"] == "refunded"
        assert item["refundedAt"] is not None

    @pytest.mark.asyncio
    async def test_an_empty_history_is_empty_rather_than_an_error(self, history):
        """The common case, and the honest one: there are no purchases yet, which is a `200` with an
        empty list, not a `404` and not a failure.
        """
        result = await credit_purchase_service.get_purchase_history("u1")
        assert result["items"] == []
        assert result["total"] == 0
        assert result["totalPages"] == 0

    @pytest.mark.asyncio
    async def test_the_price_is_formatted_and_the_raw_figure_is_kept(self, history):
        """Both, deliberately: the display string for a screen, and the minor-unit integer so a client
        is not forced to parse a currency back out of prose.
        """
        history["rows"] = [a_purchase(amount_minor=249, currency="USD")]
        history["total"] = 1
        result = await credit_purchase_service.get_purchase_history("u1")
        item = result["items"][0]
        assert item["priceFormatted"] == "$2.49"
        assert item["amountMinor"] == 249

    @pytest.mark.asyncio
    async def test_ngn_formats_in_naira(self, history):
        history["rows"] = [a_purchase(amount_minor=550_000, currency="NGN")]
        history["total"] = 1
        result = await credit_purchase_service.get_purchase_history("u1")
        assert result["items"][0]["priceFormatted"] == "₦5,500"

    @pytest.mark.asyncio
    async def test_the_page_size_bound_still_holds(self, history):
        with pytest.raises(Exception):  # noqa: B017 - ValidationError, asserted by not-raising below
            await credit_purchase_service.get_purchase_history("u1", page_size=0)


class TestTheCreditTablesAreGone:
    def test_the_models_are_deleted(self):
        """Decision H drops both. Leaving the models mapped against dropped tables is how a later query
        compiles fine and fails at runtime, so the mapping goes with the tables.
        """
        from src.domains.billing import db_models

        assert not hasattr(db_models, "CreditPack")
        assert not hasattr(db_models, "CreditPurchaseTransaction")

    def test_migration_072_drops_them(self):
        # Read the migration source rather than importing it, since alembic modules run under a
        # migration context. The two drops and the order (child before parent) are the contract.
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "072_drop_credit_tables.py"
        )
        source = path.read_text(encoding="utf-8")
        assert 'op.drop_table("CreditPurchaseTransaction")' in source
        assert 'op.drop_table("CreditPack")' in source
        # Child first: the FK from the transaction points at the pack.
        assert source.index("CreditPurchaseTransaction") < source.index(
            'op.drop_table("CreditPack")'
        )
