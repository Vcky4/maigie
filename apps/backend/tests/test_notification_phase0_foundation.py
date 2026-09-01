"""Phase 0 notification rollout gates and operational lifecycle metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.routing import APIRoute

from src.config import Settings
from src.domains.notifications import service
from src.domains.notifications.feature_flags import (
    capability_enabled_for,
    stable_user_cohort,
)
from src.domains.notifications.routes import router


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_unimplemented_notification_capabilities_fail_closed() -> None:
    settings = _settings()

    assert capability_enabled_for("EMAIL", "user-1", settings=settings) is False
    assert capability_enabled_for("WEB_PUSH", "user-1", settings=settings) is False
    assert capability_enabled_for("INTELLIGENCE", "user-1", settings=settings) is False
    # Code fallback is fail-closed. Deployment templates may enable the sender at
    # a zero-percent cohort without making any user eligible.
    assert settings.MOBILE_PUSH_ENABLED is False


def test_rollout_order_is_kill_switch_then_deny_then_allow_then_cohort() -> None:
    user_id = "stable-user"
    cohort = stable_user_cohort(user_id)
    settings = _settings(
        WEB_PUSH_ENABLED=True,
        WEB_PUSH_DENYLIST=["denied", "both"],
        WEB_PUSH_ALLOWLIST=["allowed", "both"],
        WEB_PUSH_INTERNAL_ALLOWLIST=["internal"],
        WEB_PUSH_ROLLOUT_PERCENT=cohort + 1,
    )

    assert capability_enabled_for("WEB_PUSH", "denied", settings=settings) is False
    assert capability_enabled_for("WEB_PUSH", "both", settings=settings) is False
    assert capability_enabled_for("WEB_PUSH", "allowed", settings=settings) is True
    assert capability_enabled_for("WEB_PUSH", "internal", settings=settings) is True
    assert capability_enabled_for("WEB_PUSH", user_id, settings=settings) is True

    disabled = settings.model_copy(update={"WEB_PUSH_ENABLED": False})
    assert capability_enabled_for("WEB_PUSH", "allowed", settings=disabled) is False


def test_rollout_cohort_is_stable_and_bounded() -> None:
    first = stable_user_cohort("user-123")

    assert first == stable_user_cohort("user-123")
    assert 0 <= first <= 99


@pytest.mark.asyncio
async def test_lifecycle_metrics_are_database_backed_and_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 8, 31, 12, tzinfo=UTC)
    expected = {
        "generatedAt": generated_at,
        "actionableDeliveries": [],
        "failuresLast24Hours": [],
        "interactionsLast24Hours": [],
    }
    captured: dict[str, datetime] = {}

    async def fake_metrics(*, now: datetime) -> dict[str, object]:
        captured["now"] = now
        return expected

    monkeypatch.setattr(service.notification_repo, "lifecycle_metrics", fake_metrics)

    assert await service.lifecycle_metrics() == expected
    assert captured["now"].tzinfo is UTC


def test_operational_metrics_route_requires_staff_authentication() -> None:
    route = next(
        item
        for item in router.routes
        if isinstance(item, APIRoute) and item.path == "/operations/metrics"
    )
    dependency_names = {
        dependency.call.__name__
        for dependency in route.dependant.dependencies
        if dependency.call is not None
    }

    assert "get_staff_user" in dependency_names


@pytest.mark.asyncio
async def test_notification_settings_defaults_are_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    async def fake_snapshot(user_id: str) -> dict[str, Any]:
        assert user_id == "user-1"
        return {
            "policy": None,
            "preferences": [],
            "legacy": SimpleNamespace(
                notifications=True,
                timezone="Africa/Lagos",
                timezone_source="MANUAL",
                email_schedule_reminder=False,
                email_weekly_tips=False,
                push_schedule_reminder=False,
                push_study_tips=False,
            ),
            "profile": None,
        }

    monkeypatch.setattr(service.notification_repo, "notification_settings_snapshot", fake_snapshot)

    result = await service.get_notification_settings(user_id="user-1")
    by_category = {item.category: item for item in result.categories}
    assert result.engagement_enabled is True
    assert result.max_daily_notifications == 5
    assert by_category["LEARNING"].in_app is True
    assert by_category["PROGRESS"].in_app is True
    assert by_category["SOCIAL_CLASSROOM"].in_app is False
    assert all(item.mobile_push is False for item in result.categories)
    assert all(item.email_frequency == "OFF" for item in result.categories)
    assert result.web_push_available is False
    assert result.email_open_tracking is False


@pytest.mark.asyncio
async def test_notification_settings_update_dual_writes_legacy_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from src.domains.notifications.models import (
        NotificationCategorySetting,
        NotificationSettingsUpdate,
    )

    current: dict[str, Any] = {
        "policy": SimpleNamespace(timezone="Europe/London", timezone_source="MANUAL"),
        "preferences": [],
        "legacy": None,
        "profile": None,
    }
    captured: dict[str, Any] = {}

    async def fake_snapshot(user_id: str) -> dict[str, Any]:
        return current

    async def fake_update(user_id: str, **values: Any) -> None:
        captured["user_id"] = user_id
        captured.update(values)

    async def fake_get(*, user_id: str) -> str:
        return "updated"

    monkeypatch.setattr(service.notification_repo, "notification_settings_snapshot", fake_snapshot)
    monkeypatch.setattr(service.notification_repo, "update_notification_settings", fake_update)
    monkeypatch.setattr(service, "get_notification_settings", fake_get)

    request = NotificationSettingsUpdate(
        engagement_enabled=True,
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
        max_daily_notifications=3,
        digest_local_time="09:00",
        digest_day_of_week=0,
        categories=[
            NotificationCategorySetting(
                category="LEARNING", in_app=True, mobile_push=True, email_frequency="IMMEDIATE"
            ),
            NotificationCategorySetting(
                category="PROGRESS", in_app=True, mobile_push=False, email_frequency="WEEKLY"
            ),
            NotificationCategorySetting(
                category="SOCIAL_CLASSROOM",
                in_app=False,
                mobile_push=False,
                email_frequency="OFF",
            ),
            NotificationCategorySetting(
                category="PRODUCT_UPDATES",
                in_app=False,
                mobile_push=False,
                email_frequency="OFF",
            ),
        ],
    )

    assert (
        await service.update_notification_settings(user_id="user-1", request=request) == "updated"
    )
    assert captured["user_id"] == "user-1"
    assert len(captured["preferences"]) == 15
    assert captured["legacy_values"] == {
        "notifications": True,
        "email_schedule_reminder": True,
        "email_weekly_tips": True,
        "email_morning_schedule": False,
        "push_schedule_reminder": True,
        "push_study_tips": True,
    }
    policy = captured["policy_values"]
    assert policy["timezone"] == "Europe/London"
    assert policy["max_daily_notifications"] == 3
