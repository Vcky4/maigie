"""The landing draft sweep deletes what the privacy policy says it deletes, and nothing else.

Four properties are load-bearing and each has a test: personal data goes at expiry rather than at
deletion, a claimed draft survives as a conversion record, an unexpired draft is untouched however
many times the sweep runs, and the whole thing is a no-op when switched off.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.landing_drafts import retention  # noqa: E402

NOW = datetime(2026, 9, 10, 4, 10, tzinfo=UTC)


def _settings(**overrides) -> Settings:
    base = {
        "LANDING_DRAFT_RETENTION_ENABLED": True,
        "LANDING_DRAFT_RETENTION_GRACE_DAYS": 30,
        "LANDING_DRAFT_RETENTION_BATCH": 500,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class _Row:
    """The subset of a LandingDraft the sweep touches."""

    def __init__(self, draft_id: str, *, status: str, expires_at: datetime, email: str | None):
        self.id = draft_id
        self.status = status
        self.expires_at = expires_at
        self.email = email


class FakeRepo:
    """In-memory stand-in applying the same predicates as the real repository methods."""

    def __init__(self, rows: list[_Row]):
        self.rows = rows

    async def expire_due(self, *, limit: int = 500) -> int:
        due = [r for r in self.rows if r.status == "open" and r.expires_at <= NOW][:limit]
        for row in due:
            row.status = "expired"
        return len(due)

    async def redact_expired(self, *, now: datetime, limit: int = 500) -> int:
        due = [r for r in self.rows if r.expires_at <= now and r.email is not None][:limit]
        for row in due:
            row.email = None
        return len(due)

    async def delete_expired(self, *, cutoff: datetime, limit: int = 500) -> int:
        due = [r for r in self.rows if r.status in ("open", "expired") and r.expires_at <= cutoff][
            :limit
        ]
        for row in due:
            self.rows.remove(row)
        return len(due)


def _rows() -> list[_Row]:
    return [
        # Expired yesterday: unusable, so its email must go, but it is inside the grace window.
        _Row("recent", status="open", expires_at=NOW - timedelta(days=1), email="a@x.edu"),
        # Expired 40 days ago, unclaimed: past the grace window, so the row goes.
        _Row("ancient", status="expired", expires_at=NOW - timedelta(days=40), email="b@x.edu"),
        # Converted, and long past expiry: redacted but kept as the conversion record.
        _Row("claimed", status="claimed", expires_at=NOW - timedelta(days=40), email="c@x.edu"),
        # Still live: the sweep must not touch it at all.
        _Row("live", status="open", expires_at=NOW + timedelta(days=5), email="d@x.edu"),
    ]


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    fake = FakeRepo(_rows())
    monkeypatch.setattr(retention, "landing_draft_repo", fake)
    return fake


@pytest.mark.asyncio
async def test_disabled_is_a_no_op(repo: FakeRepo) -> None:
    counts = await retention.sweep_expired(
        now=NOW, settings=_settings(LANDING_DRAFT_RETENTION_ENABLED=False)
    )

    assert counts == {"marked": 0, "redacted": 0, "deleted": 0}
    assert len(repo.rows) == 4
    assert [r.email for r in repo.rows] == ["a@x.edu", "b@x.edu", "c@x.edu", "d@x.edu"]


@pytest.mark.asyncio
async def test_email_goes_at_expiry_not_at_deletion(repo: FakeRepo) -> None:
    await retention.sweep_expired(now=NOW, settings=_settings())

    # Expired yesterday, so the row survives the grace window — but the address does not, because it
    # was collected to hold a setup that can no longer be used.
    recent = next(r for r in repo.rows if r.id == "recent")
    assert recent.email is None
    assert recent.status == "expired", "an open row past its expiry should be marked"


@pytest.mark.asyncio
async def test_unclaimed_row_is_deleted_after_the_grace_window(repo: FakeRepo) -> None:
    counts = await retention.sweep_expired(now=NOW, settings=_settings())

    assert "ancient" not in {r.id for r in repo.rows}
    assert counts["deleted"] == 1


@pytest.mark.asyncio
async def test_claimed_draft_is_redacted_but_never_deleted(repo: FakeRepo) -> None:
    await retention.sweep_expired(now=NOW, settings=_settings())

    claimed = next(r for r in repo.rows if r.id == "claimed")
    assert claimed.status == "claimed", "the conversion record must survive the sweep"
    assert claimed.email is None, "but its copy of the address must not"


@pytest.mark.asyncio
async def test_live_draft_is_untouched(repo: FakeRepo) -> None:
    await retention.sweep_expired(now=NOW, settings=_settings())

    live = next(r for r in repo.rows if r.id == "live")
    assert live.status == "open"
    assert live.email == "d@x.edu"


@pytest.mark.asyncio
async def test_second_run_finds_nothing(repo: FakeRepo) -> None:
    first = await retention.sweep_expired(now=NOW, settings=_settings())
    second = await retention.sweep_expired(now=NOW, settings=_settings())

    assert any(first.values())
    assert second == {"marked": 0, "redacted": 0, "deleted": 0}, "the sweep must be idempotent"


@pytest.mark.asyncio
async def test_grace_window_of_zero_deletes_at_expiry(repo: FakeRepo) -> None:
    counts = await retention.sweep_expired(
        now=NOW, settings=_settings(LANDING_DRAFT_RETENTION_GRACE_DAYS=0)
    )

    # Both expired unclaimed rows go; the claimed one and the live one remain.
    assert {r.id for r in repo.rows} == {"claimed", "live"}
    assert counts["deleted"] == 2


@pytest.mark.asyncio
async def test_batching_stops_at_the_ceiling_without_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that always reports a full batch must not loop forever."""
    calls = {"n": 0}

    class _Endless:
        async def expire_due(self, *, limit: int = 500) -> int:
            calls["n"] += 1
            return limit

        async def redact_expired(self, *, now: datetime, limit: int = 500) -> int:
            return 0

        async def delete_expired(self, *, cutoff: datetime, limit: int = 500) -> int:
            return 0

    monkeypatch.setattr(retention, "landing_draft_repo", _Endless())
    monkeypatch.setattr(retention, "_MAX_BATCHES", 5)

    counts = await retention.sweep_expired(
        now=NOW, settings=_settings(LANDING_DRAFT_RETENTION_BATCH=10)
    )

    assert calls["n"] == 5
    assert counts["marked"] == 50
