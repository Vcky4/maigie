"""Tests for stubs restored during the technical-debt pass.

Each of these was a stub that succeeded silently, which is the failure mode this file
exists to prevent:

* ``cost_calculator`` returned ``0.0`` for every call, so cost, revenue and margin
  reporting all read zero no matter the usage.
* ``space_gates`` exposed a contract its callers did not use, so the two gated space
  features raised ``TypeError`` instead of being gated.
* ``audit_service.log_admin_action`` absorbed every argument into ``**kwargs`` and
  discarded them, so privileged credit adjustments left no record.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import inspect  # noqa: E402
import logging  # noqa: E402

import pytest  # noqa: E402

from src.domains.admin.services import audit_service  # noqa: E402
from src.domains.billing.services import cost_calculator as cc  # noqa: E402
from src.domains.learning_spaces.services.space_gates import (  # noqa: E402
    FREE_CHAT_GROUP_LIMIT,
    FREE_GROUP_SESSION_LIMIT,
    SpaceFeature,
    SpaceGateError,
    SpaceGateState,
    gate,
)

# ---------------------------------------------------------------------------
# cost_calculator
# ---------------------------------------------------------------------------


def test_cost_is_not_zero_for_real_usage():
    """The regression that mattered: the stub reported every call as free."""
    cost = cc.calculate_ai_cost(
        input_tokens=1_000_000, output_tokens=1_000_000, model_name="gemini-3.5-flash"
    )
    assert cost == pytest.approx(0.50 + 3.00)


def test_zero_tokens_costs_nothing():
    assert cc.calculate_ai_cost(0, 0, "gemini-3.5-flash") == 0.0


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gemini-3.5-flash", (0.50, 3.00)),
        ("models/gemini-3.5-flash", (0.50, 3.00)),
        ("  GEMINI-3.5-FLASH  ", (0.50, 3.00)),
        ("gemini-3.1-flash-lite", (0.25, 1.50)),
        ("gemini-embedding-001", (0.15, 0.0)),
        ("text-embedding-anything", (0.15, 0.0)),
        ("gemini-2.5-flash-lite", (0.10, 0.40)),
        ("gemini-2.0-flash", (0.10, 0.40)),
        ("gemini-1.5-pro", (1.25, 5.00)),
    ],
)
def test_model_ids_resolve_to_their_pricing(model, expected):
    assert cc._pricing_for_model(model) == expected


def test_unknown_model_falls_back_to_the_most_expensive_tier():
    """Better to over-state cost than to under-state it."""
    assert cc._pricing_for_model("some-unreleased-model") == (
        cc.GEMINI_15_PRO_INPUT_COST_PER_MILLION,
        cc.GEMINI_15_PRO_OUTPUT_COST_PER_MILLION,
    )


def test_missing_model_uses_the_configured_default():
    assert cc._pricing_for_model(None) == cc._pricing_for_model(cc._default_model())


def test_free_tier_earns_no_revenue():
    assert cc.calculate_revenue(500_000, 500_000, "FREE") == 0.0


def test_paid_tier_revenue_scales_with_tokens():
    assert cc.calculate_revenue(500_000, 500_000, "PREMIUM_MONTHLY") == pytest.approx(10.0)


def test_profit_margin_handles_zero_revenue_without_dividing_by_zero():
    profit, margin = cc.calculate_profit_margin(cost_usd=5.0, revenue_usd=0.0)
    assert profit == -5.0
    assert margin == 0.0


def test_profit_margin_percentage():
    profit, margin = cc.calculate_profit_margin(cost_usd=2.0, revenue_usd=10.0)
    assert profit == pytest.approx(8.0)
    assert margin == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# space_gates
# ---------------------------------------------------------------------------


def test_gate_signature_matches_its_callers():
    """``space_impl`` calls ``gate(feature, state)`` synchronously."""
    params = list(inspect.signature(gate).parameters)
    assert params == ["feature", "state"]
    assert not inspect.iscoroutinefunction(gate)


def test_gate_state_accepts_the_fields_the_callers_assemble():
    """The stub was a StrEnum, so this construction raised TypeError."""
    state = SpaceGateState(
        space_plan_active=False,
        has_any_active_addon=False,
        chat_group_count=0,
    )
    assert state.has_paid_entitlement is False


@pytest.mark.parametrize("feature", list(SpaceFeature))
def test_an_active_plan_allows_everything(feature):
    gate(
        feature, SpaceGateState(space_plan_active=True, chat_group_count=99, group_session_count=99)
    )


@pytest.mark.parametrize("feature", list(SpaceFeature))
def test_an_active_addon_allows_everything(feature):
    gate(
        feature,
        SpaceGateState(has_any_active_addon=True, chat_group_count=99, group_session_count=99),
    )


def test_free_space_gets_its_allowance_then_is_asked_to_upgrade():
    within = SpaceGateState(chat_group_count=FREE_CHAT_GROUP_LIMIT - 1)
    gate(SpaceFeature.CHAT_GROUP_CREATE, within)

    at_limit = SpaceGateState(chat_group_count=FREE_CHAT_GROUP_LIMIT)
    with pytest.raises(SpaceGateError) as excinfo:
        gate(SpaceFeature.CHAT_GROUP_CREATE, at_limit)

    # The caller reads all three of these off the exception.
    assert excinfo.value.code == "SPACE_PLAN_REQUIRED"
    assert excinfo.value.status_code == 402
    assert excinfo.value.message


def test_group_session_allowance_is_enforced_separately():
    gate(
        SpaceFeature.GROUP_SESSION_START,
        SpaceGateState(group_session_count=FREE_GROUP_SESSION_LIMIT - 1),
    )

    with pytest.raises(SpaceGateError):
        gate(
            SpaceFeature.GROUP_SESSION_START,
            SpaceGateState(group_session_count=FREE_GROUP_SESSION_LIMIT),
        )


def test_chat_group_count_does_not_gate_group_sessions():
    """The two allowances are independent; one must not consume the other."""
    gate(
        SpaceFeature.GROUP_SESSION_START, SpaceGateState(chat_group_count=99, group_session_count=0)
    )


def test_an_unknown_feature_is_denied_rather_than_permitted():
    with pytest.raises(SpaceGateError) as excinfo:
        gate("not_a_feature", SpaceGateState())  # type: ignore[arg-type]

    assert excinfo.value.code == "SPACE_FEATURE_UNKNOWN"
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# audit_service
# ---------------------------------------------------------------------------


def test_log_admin_action_signature_matches_its_caller():
    """``credit_purchase_service`` passes all of these by keyword.

    The stub declared ``(action, admin_id="", **kwargs)``, so ``admin_user_id``,
    ``resource_type``, ``resource_id`` and ``details`` were silently swallowed.
    """
    params = inspect.signature(audit_service.log_admin_action).parameters
    for expected in ("admin_user_id", "action", "resource_type", "resource_id", "details"):
        assert expected in params


async def test_admin_action_is_actually_recorded(caplog):
    with caplog.at_level(logging.INFO, logger=audit_service.__name__):
        await audit_service.log_admin_action(
            admin_user_id="admin-1",
            action="adjust_purchased_credits",
            resource_type="user",
            resource_id="user-9",
            details={"adjustment_amount": 500, "reason": "support request"},
        )

    records = [r for r in caplog.records if getattr(r, "audit", False)]
    assert len(records) == 1

    record = records[0]
    assert record.admin_user_id == "admin-1"
    assert record.action == "adjust_purchased_credits"
    assert record.resource_type == "user"
    assert record.resource_id == "user-9"
    assert record.details["adjustment_amount"] == 500
    assert record.timestamp


async def test_audit_failure_never_breaks_the_audited_operation(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(audit_service.logger, "info", boom)

    # Must not raise: the credit adjustment it describes has already happened.
    await audit_service.log_admin_action(
        admin_user_id="admin-1",
        action="adjust_purchased_credits",
        resource_type="user",
    )


async def test_admin_action_is_persisted_with_the_right_column_mapping(monkeypatch):
    """The AuditLog table exists in the database but had no model, so nothing was stored.

    The attribute names here are snake_case while the columns are the camelCase
    originals, so a mismatch would only show up against a real database.
    """
    added: list = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, obj):
            added.append(obj)

        async def commit(self):
            pass

    monkeypatch.setattr("src.shared.database.get_session_factory", lambda: (lambda: FakeSession()))

    await audit_service.log_admin_action(
        admin_user_id="admin-1",
        action="adjust_purchased_credits",
        resource_type="user",
        resource_id="user-9",
        details={"adjustment_amount": 500},
    )

    assert len(added) == 1
    row = added[0]
    assert row.admin_user_id == "admin-1"
    assert row.action_type == "adjust_purchased_credits"
    assert row.resource_type == "user"
    assert row.resource_id == "user-9"
    assert row.details == {"adjustment_amount": 500}
    # The column is timestamp-without-time-zone, so the value must be naive.
    assert row.timestamp.tzinfo is None


async def test_audit_columns_match_the_live_table():
    """Guards the mapping against the shape confirmed in the database."""
    from src.domains.admin.db_models import AuditLog

    columns = {c.name for c in AuditLog.__table__.columns}
    assert columns == {
        "id",
        "adminUserId",
        "actionType",
        "resourceType",
        "resourceId",
        "details",
        "timestamp",
    }


async def test_a_database_failure_still_leaves_the_log_record(monkeypatch, caplog):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("src.shared.database.get_session_factory", boom)

    with caplog.at_level(logging.INFO, logger=audit_service.__name__):
        await audit_service.log_admin_action(
            admin_user_id="admin-1",
            action="adjust_purchased_credits",
            resource_type="user",
        )

    assert [r for r in caplog.records if getattr(r, "audit", False)]
    assert any("Failed to persist" in r.getMessage() for r in caplog.records)
