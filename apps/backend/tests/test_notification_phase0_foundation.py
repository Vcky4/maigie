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
    # With the legacy columns retired, an absent policy is the conservative fallback: engagement is
    # off (fail-closed) regardless of what the legacy row said. The category defaults still apply
    # from no rows: in-app on for LEARNING/PROGRESS, everything else off.
    assert result.engagement_enabled is False
    assert result.max_daily_notifications == 5
    assert by_category["LEARNING"].in_app is True
    assert by_category["PROGRESS"].in_app is True
    assert by_category["SOCIAL_CLASSROOM"].in_app is False
    assert all(item.mobile_push is False for item in result.categories)
    assert all(item.email_frequency == "OFF" for item in result.categories)
    assert result.web_push_available is False
    assert result.email_open_tracking is False


@pytest.mark.asyncio
async def test_notification_settings_update_writes_policy_and_preferences_only(
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
    # The legacy dual-write is retired: the update writes only the normalized policy and preferences.
    assert "legacy_values" not in captured
    policy = captured["policy_values"]
    assert policy["timezone"] == "Europe/London"
    assert policy["max_daily_notifications"] == 3


def _matrix(**web_push_by_category: Any):
    """Build a complete settings matrix, overriding `webPush` per category."""
    from src.domains.notifications.models import (
        NotificationCategorySetting,
        NotificationSettingsUpdate,
    )

    return NotificationSettingsUpdate(
        engagement_enabled=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
        max_daily_notifications=3,
        digest_local_time="09:00",
        digest_day_of_week=0,
        categories=[
            NotificationCategorySetting(
                category=key,
                in_app=True,
                mobile_push=False,
                email_frequency="OFF",
                web_push=web_push_by_category.get(key, None),
            )
            for key in ("LEARNING", "PROGRESS", "SOCIAL_CLASSROOM", "PRODUCT_UPDATES")
        ],
    )


def _settings_harness(monkeypatch: pytest.MonkeyPatch, preferences: list[Any]) -> dict[str, Any]:
    from types import SimpleNamespace

    captured: dict[str, Any] = {}

    async def fake_snapshot(user_id: str) -> dict[str, Any]:
        return {
            "policy": SimpleNamespace(timezone="UTC", timezone_source="MANUAL"),
            "preferences": preferences,
            "legacy": None,
            "profile": None,
        }

    async def fake_update(user_id: str, **values: Any) -> None:
        captured.update(values)

    async def fake_get(*, user_id: str) -> str:
        return "updated"

    monkeypatch.setattr(service.notification_repo, "notification_settings_snapshot", fake_snapshot)
    monkeypatch.setattr(service.notification_repo, "update_notification_settings", fake_update)
    monkeypatch.setattr(service, "get_notification_settings", fake_get)
    return captured


def _preference(category: str, channel: str, *, enabled: bool, frequency: str = "IMMEDIATE"):
    from types import SimpleNamespace

    return SimpleNamespace(
        category=category,
        channel=channel,
        notification_type=None,
        enabled=enabled,
        frequency=frequency,
        digest_period=None,
    )


@pytest.mark.asyncio
async def test_web_push_consent_is_written_per_database_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _settings_harness(monkeypatch, [])

    await service.update_notification_settings(user_id="user-1", request=_matrix(LEARNING=True))

    web_push_rows = [r for r in captured["preferences"] if r["channel"] == "WEB_PUSH"]
    assert web_push_rows == [
        {
            "category": "LEARNING",
            "channel": "WEB_PUSH",
            "enabled": True,
            "frequency": "IMMEDIATE",
            "digest_period": None,
        }
    ]


@pytest.mark.asyncio
async def test_a_client_that_omits_web_push_does_not_revoke_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mobile build predating web push must not switch off a consent given on a laptop."""

    captured = _settings_harness(monkeypatch, [_preference("LEARNING", "WEB_PUSH", enabled=True)])

    await service.update_notification_settings(user_id="user-1", request=_matrix())

    web_push_rows = [r for r in captured["preferences"] if r["channel"] == "WEB_PUSH"]
    assert web_push_rows == [
        {
            "category": "LEARNING",
            "channel": "WEB_PUSH",
            "enabled": True,
            "frequency": "IMMEDIATE",
            "digest_period": None,
        }
    ], "omitting webPush must preserve the stored consent, not clear it"


@pytest.mark.asyncio
async def test_omitting_web_push_writes_nothing_where_nothing_was_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent consent stays absent, which the dispatcher already reads as not given."""

    captured = _settings_harness(monkeypatch, [])

    await service.update_notification_settings(user_id="user-1", request=_matrix())

    assert [r for r in captured["preferences"] if r["channel"] == "WEB_PUSH"] == []


@pytest.mark.asyncio
async def test_web_push_can_be_switched_off_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _settings_harness(monkeypatch, [_preference("LEARNING", "WEB_PUSH", enabled=True)])

    await service.update_notification_settings(user_id="user-1", request=_matrix(LEARNING=False))

    web_push_rows = [r for r in captured["preferences"] if r["channel"] == "WEB_PUSH"]
    assert web_push_rows[0]["enabled"] is False
    assert web_push_rows[0]["frequency"] == "OFF"


@pytest.mark.asyncio
async def test_web_push_consent_is_reported_back_from_stored_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    async def fake_snapshot(user_id: str) -> dict[str, Any]:
        return {
            "policy": None,
            "preferences": [_preference("LEARNING", "WEB_PUSH", enabled=True)],
            "legacy": SimpleNamespace(
                notifications=True,
                timezone="UTC",
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
    assert by_category["LEARNING"].web_push is True
    assert by_category["PROGRESS"].web_push is False, "consent is per category, never inherited"
