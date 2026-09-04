"""`update_block` attributes a study-session reminder on start, not only completion.

A study-session reminder's real job is to get the learner to *start* the block; many learners study
it and never tick "complete". So the outcome seam fires on the null->set transition of either
`started_at` or `completed_at`, whichever comes first, and records exactly once because
`record_action` is idempotent per (notification, entity). These tests pin that: a start fires it, an
edit that touches neither timestamp does not, and re-saving an already-started block does not re-fire.
"""

from types import SimpleNamespace

import pytest

from src.domains.progress.services import schedule_service


class _Repo:
    """Stands in for `progress_repo`: returns a `block` for `find_block`, an `updated` for
    `update_block`, so the test controls the before/after timestamps the seam compares."""

    def __init__(self, *, block, updated):
        self._block = block
        self._updated = updated
        self.update_calls: list[tuple[str, dict]] = []

    async def find_block(self, block_id, user_id):
        return self._block

    async def update_block(self, block_id, data):
        self.update_calls.append((block_id, data))
        return self._updated


def _block(*, started_at=None, completed_at=None):
    return SimpleNamespace(
        id="block-1",
        started_at=started_at,
        completed_at=completed_at,
        google_calendar_event_id=None,
    )


@pytest.fixture
def _no_calendar(monkeypatch):
    """The sync import is inside `update_block`; make it a no-op so the test never touches Google."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(
        "src.integrations.google_calendar.sync_schedule_block", _noop, raising=False
    )


def _capture_record_action(monkeypatch):
    calls: list[dict] = []

    async def _record(*, user_id, source_entity_id, source_entity_type=None):
        calls.append(
            {
                "user_id": user_id,
                "source_entity_id": source_entity_id,
                "source_entity_type": source_entity_type,
            }
        )
        return True

    from src.domains.notifications import service as notification_service

    monkeypatch.setattr(notification_service, "record_action", _record)
    return calls


@pytest.mark.asyncio
async def test_starting_a_block_records_the_action(monkeypatch, _no_calendar):
    repo = _Repo(
        block=_block(started_at=None),
        updated=_block(started_at="2026-09-03T09:00:00+00:00"),
    )
    monkeypatch.setattr(schedule_service, "progress_repo", repo)
    calls = _capture_record_action(monkeypatch)

    await schedule_service.update_block(
        block_id="block-1", user_id="u1", data={"startedAt": "2026-09-03T09:00:00+00:00"}
    )

    assert calls == [
        {"user_id": "u1", "source_entity_id": "block-1", "source_entity_type": "schedule_block"}
    ]


@pytest.mark.asyncio
async def test_a_plain_edit_does_not_record(monkeypatch, _no_calendar):
    """Renaming a block touches neither timestamp — nothing to attribute."""
    repo = _Repo(
        block=_block(started_at=None, completed_at=None),
        updated=_block(started_at=None, completed_at=None),
    )
    monkeypatch.setattr(schedule_service, "progress_repo", repo)
    calls = _capture_record_action(monkeypatch)

    await schedule_service.update_block(block_id="block-1", user_id="u1", data={"title": "Renamed"})

    assert calls == []


@pytest.mark.asyncio
async def test_re_saving_an_already_started_block_does_not_refire(monkeypatch, _no_calendar):
    """Already started before the edit, still started after: no null->set transition, no re-fire."""
    already = "2026-09-03T09:00:00+00:00"
    repo = _Repo(
        block=_block(started_at=already),
        updated=_block(started_at=already),
    )
    monkeypatch.setattr(schedule_service, "progress_repo", repo)
    calls = _capture_record_action(monkeypatch)

    await schedule_service.update_block(
        block_id="block-1", user_id="u1", data={"title": "Renamed while started"}
    )

    assert calls == []


@pytest.mark.asyncio
async def test_completing_a_block_still_records(monkeypatch, _no_calendar):
    """The original completion seam is preserved alongside the new start one."""
    repo = _Repo(
        block=_block(completed_at=None),
        updated=_block(completed_at="2026-09-03T10:00:00+00:00"),
    )
    monkeypatch.setattr(schedule_service, "progress_repo", repo)
    calls = _capture_record_action(monkeypatch)

    await schedule_service.update_block(
        block_id="block-1", user_id="u1", data={"completedAt": "2026-09-03T10:00:00+00:00"}
    )

    assert calls == [
        {"user_id": "u1", "source_entity_id": "block-1", "source_entity_type": "schedule_block"}
    ]
