"""Phase 0 notification rollout gates and operational lifecycle metrics."""

from __future__ import annotations

from datetime import UTC, datetime

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
