"""Emitting ACTIONED, the outcome signal attribution is built on.

The contract that matters here is safety, not cleverness: recording an outcome must never be able
to fail the learner's actual action, which is already done by the time the recorder runs. So these
pin that a lookup miss records nothing (rather than raising), that a write fault is swallowed, and
that a real completion maps back to its notification exactly once.
"""

from __future__ import annotations

import pytest

from src.domains.notifications import service


class _Repo:
    """A stand-in for the notification repository, recording what the service asked of it."""

    def __init__(self, *, found: str | None, raise_on_find: bool = False):
        self._found = found
        self._raise_on_find = raise_on_find
        self.appended: list[tuple[str, dict]] = []

    async def find_actionable_notification(self, user_id, **kwargs):
        if self._raise_on_find:
            raise RuntimeError("database is down")
        return self._found

    async def append_interaction(self, user_id, notification_id, values):
        self.appended.append((notification_id, values))
        return object(), True


@pytest.mark.asyncio
async def test_a_completion_records_actioned_against_the_notification(monkeypatch):
    repo = _Repo(found="notif-1")
    monkeypatch.setattr(service, "notification_repo", repo)

    recorded = await service.record_action(
        user_id="u1", source_entity_id="block-1", source_entity_type="schedule_block"
    )

    assert recorded is True
    assert len(repo.appended) == 1
    notification_id, values = repo.appended[0]
    assert notification_id == "notif-1"
    assert values["event"] == "ACTIONED"
    assert values["surface"] == "SYSTEM"
    # Deterministic, so replaying the same completion cannot double-count.
    assert values["idempotency_id"] == "actioned:notif-1:block-1"


@pytest.mark.asyncio
async def test_no_matching_notification_records_nothing(monkeypatch):
    repo = _Repo(found=None)
    monkeypatch.setattr(service, "notification_repo", repo)

    recorded = await service.record_action(user_id="u1", source_entity_id="block-unheralded")

    # The learner reached the entity on their own; that is not an error and not an outcome.
    assert recorded is False
    assert repo.appended == []


@pytest.mark.asyncio
async def test_a_lookup_fault_never_reaches_the_caller(monkeypatch):
    repo = _Repo(found=None, raise_on_find=True)
    monkeypatch.setattr(service, "notification_repo", repo)

    # The action is already saved; instrumentation failing must not surface as an error.
    recorded = await service.record_action(user_id="u1", source_entity_id="block-1")

    assert recorded is False
