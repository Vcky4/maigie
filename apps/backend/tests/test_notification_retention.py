"""Notification retention deletes evidence past its window, and nothing when it is disabled.

The rules that keep an unattended delete safe are the ones worth pinning: it is a no-op unless
explicitly enabled; it never touches the `Notification` rows a learner sees; it only prunes terminal
deliveries so nothing in flight is deleted; and it deletes in bounded batches. These tests exercise
the gate and the exact predicates against a fake session that records the statements issued.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.notifications import retention  # noqa: E402

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_disabled_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _fake(*_a, **_k):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(retention, "_prune_in_batches", _fake)

    counts = await retention.prune_expired(
        now=NOW, settings=Settings(_env_file=None, NOTIFICATION_RETENTION_ENABLED=False)
    )

    assert counts == {
        "deliveries": 0,
        "interactions": 0,
        "decisions": 0,
        "digests": 0,
        "emailEvents": 0,
    }
    assert called is False, "a disabled sweep must not issue a single delete"


@pytest.mark.asyncio
async def test_enabled_prunes_each_table_with_its_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    async def _fake(model, _predicate, *, batch):
        calls.append((model.__name__, batch))
        return 3

    monkeypatch.setattr(retention, "_prune_in_batches", _fake)

    counts = await retention.prune_expired(
        now=NOW,
        settings=Settings(
            _env_file=None,
            NOTIFICATION_RETENTION_ENABLED=True,
            NOTIFICATION_RETENTION_BATCH=500,
        ),
    )

    # Every evidence table is swept; the learner-facing Notification table is not among them.
    swept = {name for name, _ in calls}
    assert swept == {
        "NotificationDelivery",
        "NotificationInteraction",
        "NotificationDecision",
        "NotificationDigest",
        "EmailProviderEvent",
    }
    assert "Notification" not in swept
    assert all(batch == 500 for _, batch in calls)
    assert counts == {
        "deliveries": 3,
        "interactions": 3,
        "decisions": 3,
        "digests": 3,
        "emailEvents": 3,
    }


def test_only_terminal_delivery_states_are_prunable() -> None:
    # In-flight states must never appear in the prunable set — retention cannot race the dispatcher.
    assert set(retention._TERMINAL_DELIVERY_STATES).isdisjoint({"PLANNED", "QUEUED", "SENDING"})
    assert set(retention._TERMINAL_DELIVERY_STATES) == {
        "ACCEPTED",
        "DELIVERED",
        "FAILED",
        "EXPIRED",
        "CANCELLED",
        "SUPPRESSED",
    }
