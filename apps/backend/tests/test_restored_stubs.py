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
from datetime import UTC, datetime  # noqa: E402

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
    assert cost == pytest.approx(1.50 + 9.00)


def test_zero_tokens_costs_nothing():
    assert cc.calculate_ai_cost(0, 0, "gemini-3.5-flash") == 0.0


@pytest.mark.parametrize(
    "model,expected",
    [
        # $1.50 / $9.00, corrected in Phase 0 — the table said 0.50 / 3.00 and this agreed with it.
        ("gemini-3.5-flash", (1.50, 9.00)),
        ("models/gemini-3.5-flash", (1.50, 9.00)),
        ("  GEMINI-3.5-FLASH  ", (1.50, 9.00)),
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


# The gate's own rules live in ``test_space_gates.py``, the recovered spec covering every
# feature, limit, error code and status code. What is asserted here is the integration
# contract with ``space_impl``, which that spec predates: the call shape, the exact keyword
# fields the caller assembles, and the attributes it reads off the exception.


def test_gate_signature_matches_its_callers():
    """``space_impl`` calls ``gate(feature, state)`` synchronously, without awaiting."""
    params = list(inspect.signature(gate).parameters)
    assert params == ["feature", "state"]
    assert not inspect.iscoroutinefunction(gate)


def test_gate_state_accepts_the_fields_the_caller_assembles():
    """The stub was a StrEnum, so this construction raised TypeError.

    These are the exact keywords used at both ``space_impl`` call sites.
    """
    chat = SpaceGateState(
        space_plan_active=False,
        has_any_active_addon=False,
        chat_group_count=0,
    )
    session = SpaceGateState(
        space_plan_active=False,
        has_any_active_addon=False,
        group_session_count=0,
    )
    assert gate(SpaceFeature.CHAT_GROUP_CREATE, chat) is True
    assert gate(SpaceFeature.GROUP_SESSION_START, session) is True


def test_the_error_carries_everything_the_caller_puts_in_the_response():
    """``space_impl`` reads ``status_code``, ``code`` and ``message`` off the exception."""
    with pytest.raises(SpaceGateError) as excinfo:
        gate(SpaceFeature.CHAT_GROUP_CREATE, SpaceGateState(chat_group_count=99))

    error = excinfo.value
    assert isinstance(error.status_code, int)
    assert error.code
    assert error.message


def test_chat_group_count_does_not_gate_group_sessions():
    """The two allowances are independent; one must not consume the other."""
    assert (
        gate(
            SpaceFeature.GROUP_SESSION_START,
            SpaceGateState(chat_group_count=99, group_session_count=0),
        )
        is True
    )


def test_the_free_limits_are_the_recovered_values_not_a_guess():
    """An earlier pass guessed both; the group-session limit was wrong.

    Pinned so that changing either has to be deliberate.
    """
    assert FREE_CHAT_GROUP_LIMIT == 1
    assert FREE_GROUP_SESSION_LIMIT == 3


# ---------------------------------------------------------------------------
# audit_service
# ---------------------------------------------------------------------------


def test_log_admin_action_signature_matches_its_caller():
    """``credit_purchase_service`` passes all of these by keyword.

    The stub declared ``(action, admin_id="", **kwargs)``, so ``admin_user_id``,
    ``resource_type``, ``resource_id`` and ``details`` were silently swallowed.
    """
    params = inspect.signature(audit_service.log_admin_action).parameters
    for expected in (
        "admin_user_id",
        "action",
        "resource_type",
        "resource_id",
        "details",
    ):
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


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------


def test_recurring_rules_convert_to_rrule():
    from src.integrations.google_calendar.service import google_calendar_service as gcal

    start = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)  # a Sunday
    assert gcal._convert_recurring_rule_to_rrule("DAILY", start) == "RRULE:FREQ=DAILY"
    assert gcal._convert_recurring_rule_to_rrule("MONTHLY", start) == "RRULE:FREQ=MONTHLY"
    assert gcal._convert_recurring_rule_to_rrule("YEARLY", start) == "RRULE:FREQ=YEARLY"
    # A weekly repeat is anchored to the day the block starts on.
    assert gcal._convert_recurring_rule_to_rrule("weekly", start) == "RRULE:FREQ=WEEKLY;BYDAY=SU"


def test_an_existing_rrule_is_passed_through_untouched():
    from src.integrations.google_calendar.service import google_calendar_service as gcal

    start = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    rule = "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"
    assert gcal._convert_recurring_rule_to_rrule(rule, start) == rule


@pytest.mark.parametrize("rule", ["", None, "every other tuesday"])
def test_an_unusable_rule_produces_no_recurrence_rather_than_a_bad_one(rule):
    from src.integrations.google_calendar.service import google_calendar_service as gcal

    start = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    assert gcal._convert_recurring_rule_to_rrule(rule, start) is None


def test_event_body_sends_utc_and_omits_recurrence_when_there_is_none():
    from types import SimpleNamespace

    from src.integrations.google_calendar.service import google_calendar_service as gcal

    block = SimpleNamespace(
        title="Revise thermodynamics",
        description="Chapter 4",
        start_at=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 9, 11, 0, tzinfo=UTC),
        recurring_rule=None,
    )
    body = gcal._event_body(block)

    assert body["summary"] == "Revise thermodynamics"
    assert body["start"]["timeZone"] == "UTC"
    assert body["end"]["dateTime"].startswith("2026-08-09T11:00")
    assert "recurrence" not in body


def test_naive_block_times_are_treated_as_utc_not_local():
    from types import SimpleNamespace

    from src.integrations.google_calendar.service import google_calendar_service as gcal

    block = SimpleNamespace(
        title="T",
        description=None,
        start_at=datetime(2026, 8, 9, 10, 0),
        end_at=datetime(2026, 8, 9, 11, 0),
        recurring_rule="DAILY",
    )
    body = gcal._event_body(block)

    assert body["start"]["dateTime"].endswith("+00:00")
    assert body["recurrence"] == ["RRULE:FREQ=DAILY"]
    # A missing description must be sent as empty, not the string "None".
    assert body["description"] == ""


# ---------------------------------------------------------------------------
# AI usage records
# ---------------------------------------------------------------------------


async def test_ai_usage_is_recorded_with_the_callers_keyword_names(monkeypatch):
    """The stub required a `scope` argument its caller never passed, so every call raised.

    The call site swallows exceptions, so nothing surfaced.
    """
    from src.domains.billing.services import usage_tracking

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

    await usage_tracking.emit_ai_usage(
        user_id="u1",
        usage_scope=usage_tracking.PERSONAL_USAGE_SCOPE,
        space_id=None,
        provider="gemini",
        model=None,
        feature="ai_course_generation",
        input_tokens=10,
        output_tokens=20,
        request_count=1,
    )

    assert len(added) == 1
    row = added[0]
    assert row.user_id == "u1"
    assert row.usage_scope == "personal"
    assert row.feature == "ai_course_generation"
    assert row.input_tokens == 10
    assert row.output_tokens == 20


async def test_usage_recording_failure_never_breaks_the_generation(monkeypatch):
    from src.domains.billing.services import usage_tracking

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("src.shared.database.get_session_factory", boom)

    # Must not raise: the tokens were already spent.
    await usage_tracking.emit_ai_usage(user_id="u1", feature="f")


def test_circle_usage_scope_is_namespaced():
    from src.domains.billing.services import usage_tracking

    assert usage_tracking.build_circle_usage_scope("sp1") == "circle:sp1"
    assert usage_tracking.PERSONAL_USAGE_SCOPE == "personal"


# ---------------------------------------------------------------------------
# Websocket event bus
# ---------------------------------------------------------------------------


async def test_ws_event_is_delivered_with_a_type_and_timestamp(monkeypatch):
    from src.shared.infrastructure import ws_event_bus

    sent: list[tuple[str, dict]] = []

    class FakeManager:
        async def send_to_user(self, user_id, message):
            sent.append((user_id, message))

    monkeypatch.setattr("src.core.websocket.manager", FakeManager())

    await ws_event_bus.publish_ws_event(
        user_id="u1", event_type="CREDITS_GRANTED", payload={"credits": 500}
    )

    assert len(sent) == 1
    user_id, message = sent[0]
    assert user_id == "u1"
    assert message["type"] == "CREDITS_GRANTED"
    assert message["credits"] == 500
    assert message["timestamp"]


async def test_ws_event_without_a_user_is_a_no_op(monkeypatch):
    from src.shared.infrastructure import ws_event_bus

    sent: list = []

    class FakeManager:
        async def send_to_user(self, user_id, message):
            sent.append(user_id)

    monkeypatch.setattr("src.core.websocket.manager", FakeManager())

    await ws_event_bus.publish_ws_event(event_type="X", payload={})

    assert sent == []


async def test_a_closed_socket_does_not_fail_the_publisher(monkeypatch):
    from src.shared.infrastructure import ws_event_bus

    class FakeManager:
        async def send_to_user(self, user_id, message):
            raise RuntimeError("socket closed")

    monkeypatch.setattr("src.core.websocket.manager", FakeManager())

    # Must not raise: the purchase it announces has already completed.
    await ws_event_bus.publish_ws_event(user_id="u1", event_type="X", payload={})
