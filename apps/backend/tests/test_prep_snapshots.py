"""Tests for readiness snapshots and the trend they enable (no DB required).

The trend is the one part of Prepare that needs stored history, because topic
mastery is a mutable float: once it changes, the previous value is gone. These
tests pin the two properties that matter — the writer is idempotent per day, and
the trend never invents a day it did not capture.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import prep_readiness, prep_snapshot_service
from src.shared.exceptions import NotFoundError

OWNER = "user-owner"
INTRUDER = "user-intruder"
TODAY = datetime.now(UTC).date()


def _progress(**overrides) -> prep_readiness.PrepProgress:
    defaults = {
        "topics_total": 4,
        "topics_strong": 2,
        "topics_focus": 1,
        "topics_assessed": 3,
        "questions_answered": 10,
        "questions_correct": 7,
        "quizzes_taken": 2,
        "practice_seconds": 600,
        "mastery_sum": 300.0,
    }
    return prep_readiness.PrepProgress(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self):
        self.preps: dict[tuple[str, str], SimpleNamespace] = {}
        # (prep_id, captured_on) -> values
        self.snapshots: dict[tuple[str, date], dict] = {}
        self.candidates: list[SimpleNamespace] = []
        self.upserts = 0

    def add_prep(
        self,
        prep_id: str,
        user_id: str,
        *,
        target_readiness: int | None = None,
        created_at: datetime | None = None,
        exam_date: datetime | None = None,
    ):
        self.preps[(prep_id, user_id)] = SimpleNamespace(
            id=prep_id,
            user_id=user_id,
            target_readiness=target_readiness,
            created_at=created_at or datetime(2026, 8, 1, tzinfo=UTC),
            exam_date=exam_date or datetime(2026, 8, 31, tzinfo=UTC),
        )

    def add_candidate(self, prep_id: str):
        self.candidates.append(SimpleNamespace(id=prep_id))

    async def list_exam_preps_by_ids(self, prep_ids: list[str]):
        """Batch load, unscoped by user — as the daily writer needs."""
        wanted = set(prep_ids)
        return [prep for (prep_id, _), prep in self.preps.items() if prep_id in wanted]

    def add_snapshot(self, prep_id: str, captured_on: date, **values):
        self.snapshots[(prep_id, captured_on)] = {
            "progress_percent": 50.0,
            "average_mastery_percent": 75.0,
            "topics_total": 4,
            "topics_strong": 2,
            "topics_focus": 1,
            "topics_assessed": 3,
            "questions_answered": 10,
            "accuracy_percent": 70.0,
            "quizzes_taken": 2,
            "target_percent": None,
            **values,
        }

    async def find_exam_prep(self, prep_id: str, user_id: str):
        return self.preps.get((prep_id, user_id))

    async def upsert_readiness_snapshot(self, *, prep_id: str, captured_on: date, values: dict):
        self.upserts += 1
        self.snapshots[(prep_id, captured_on)] = dict(values)
        return SimpleNamespace(prep_id=prep_id, captured_on=captured_on, **values)

    async def list_readiness_snapshots(self, prep_id: str, *, since: date):
        rows = [
            SimpleNamespace(prep_id=pid, captured_on=day, **values)
            for (pid, day), values in self.snapshots.items()
            if pid == prep_id and day >= since
        ]
        return sorted(rows, key=lambda row: row.captured_on)

    async def list_snapshot_candidate_preps(self, *, skip: int = 0, take: int = 100):
        return self.candidates[skip : skip + take]


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(prep_snapshot_service, "repo", fake)
    return fake


@pytest.fixture
def progress_loader(monkeypatch):
    """Serve progress per preparation without touching aggregates."""
    values: dict[str, prep_readiness.PrepProgress] = {}

    async def _load(prep_ids):
        return {pid: values.get(pid, _progress()) for pid in prep_ids}

    monkeypatch.setattr(prep_readiness, "load_for_preparations", _load)
    return values


# ---------------------------------------------------------------------------
# TestCapture
# ---------------------------------------------------------------------------


class TestCapture:
    async def test_writes_one_row_per_preparation(self, repo, progress_loader):
        written = await prep_snapshot_service.capture_for_preparations(
            ["prep-1", "prep-2"], captured_on=TODAY
        )

        assert written == 2
        assert ("prep-1", TODAY) in repo.snapshots
        assert ("prep-2", TODAY) in repo.snapshots

    async def test_snapshot_matches_the_shared_helper(self, repo, progress_loader):
        """A stored day must not disagree with what the dashboard showed."""
        progress_loader["prep-1"] = _progress(topics_total=4, topics_strong=3, mastery_sum=340.0)

        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=TODAY)

        stored = repo.snapshots[("prep-1", TODAY)]
        expected = progress_loader["prep-1"]
        assert stored["progress_percent"] == expected.progress_percent
        assert stored["average_mastery_percent"] == expected.average_mastery_percent
        assert stored["accuracy_percent"] == expected.accuracy_percent
        assert stored["topics_assessed"] == expected.topics_assessed

    async def test_recapturing_the_same_day_does_not_duplicate(self, repo, progress_loader):
        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=TODAY)
        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=TODAY)

        assert len([k for k in repo.snapshots if k[0] == "prep-1"]) == 1

    async def test_different_days_are_separate_rows(self, repo, progress_loader):
        yesterday = TODAY - timedelta(days=1)

        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=yesterday)
        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=TODAY)

        assert len([k for k in repo.snapshots if k[0] == "prep-1"]) == 2

    async def test_unmeasurable_readiness_is_stored_as_null_not_zero(self, repo, progress_loader):
        """A preparation with no topics has no measurable readiness. Storing 0
        would draw a chart flatlining at the bottom instead of showing no point."""
        progress_loader["prep-1"] = _progress(
            topics_total=0,
            topics_strong=0,
            topics_focus=0,
            topics_assessed=0,
            questions_answered=0,
            questions_correct=0,
            mastery_sum=0.0,
        )

        await prep_snapshot_service.capture_for_preparations(["prep-1"], captured_on=TODAY)

        stored = repo.snapshots[("prep-1", TODAY)]
        assert stored["average_mastery_percent"] is None
        assert stored["accuracy_percent"] is None

    async def test_empty_list_writes_nothing(self, repo, progress_loader):
        assert await prep_snapshot_service.capture_for_preparations([]) == 0
        assert repo.snapshots == {}

    async def test_one_failure_does_not_lose_the_batch(self, repo, progress_loader, monkeypatch):
        original = repo.upsert_readiness_snapshot

        async def _flaky(*, prep_id, captured_on, values):
            if prep_id == "prep-2":
                raise RuntimeError("write failed")
            return await original(prep_id=prep_id, captured_on=captured_on, values=values)

        monkeypatch.setattr(repo, "upsert_readiness_snapshot", _flaky)

        written = await prep_snapshot_service.capture_for_preparations(
            ["prep-1", "prep-2", "prep-3"], captured_on=TODAY
        )

        assert written == 2
        assert ("prep-1", TODAY) in repo.snapshots
        assert ("prep-3", TODAY) in repo.snapshots


class TestCaptureAll:
    async def test_captures_every_candidate(self, repo, progress_loader):
        for index in range(3):
            repo.add_candidate(f"prep-{index}")

        written, seen = await prep_snapshot_service.capture_all(captured_on=TODAY)

        assert (written, seen) == (3, 3)

    async def test_no_preparations_is_not_an_error(self, repo, progress_loader):
        assert await prep_snapshot_service.capture_all() == (0, 0)

    async def test_batches_are_paged_through(self, repo, progress_loader, monkeypatch):
        monkeypatch.setattr(prep_snapshot_service, "_BATCH_SIZE", 2)
        for index in range(5):
            repo.add_candidate(f"prep-{index}")

        written, seen = await prep_snapshot_service.capture_all(captured_on=TODAY)

        assert (written, seen) == (5, 5)


# ---------------------------------------------------------------------------
# TestTrend
# ---------------------------------------------------------------------------


class TestTrend:
    async def test_returns_captured_points_oldest_first(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_snapshot("prep-1", TODAY, progress_percent=60.0)
        repo.add_snapshot("prep-1", TODAY - timedelta(days=2), progress_percent=40.0)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1")

        assert [point["progressPercent"] for point in trend["points"]] == [40.0, 60.0]

    async def test_a_new_preparation_has_an_empty_series(self, repo):
        """No history is reported as none, not projected backwards from today."""
        repo.add_prep("prep-1", OWNER)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1")

        assert trend["points"] == []
        assert trend["preparationId"] == "prep-1"

    async def test_window_excludes_older_snapshots(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_snapshot("prep-1", TODAY, progress_percent=60.0)
        repo.add_snapshot("prep-1", TODAY - timedelta(days=90), progress_percent=10.0)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1", days=30)

        assert len(trend["points"]) == 1
        assert trend["points"][0]["progressPercent"] == 60.0

    async def test_window_is_capped(self, repo):
        repo.add_prep("prep-1", OWNER)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1", days=100_000)

        assert trend["days"] == prep_snapshot_service.MAX_TREND_DAYS

    async def test_window_has_a_floor(self, repo):
        repo.add_prep("prep-1", OWNER)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1", days=0)

        assert trend["days"] == 1

    async def test_nulls_survive_to_the_client(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_snapshot("prep-1", TODAY, average_mastery_percent=None, accuracy_percent=None)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1")

        assert trend["points"][0]["averageMasteryPercent"] is None
        assert trend["points"][0]["accuracyPercent"] is None

    async def test_another_users_preparation_is_not_found(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_snapshot("prep-1", TODAY)

        with pytest.raises(NotFoundError):
            await prep_snapshot_service.get_trend(user_id=INTRUDER, prep_id="prep-1")

    async def test_unknown_preparation_is_not_found(self, repo):
        with pytest.raises(NotFoundError):
            await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="nope")

    async def test_other_preparations_snapshots_are_excluded(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_snapshot("prep-1", TODAY, progress_percent=60.0)
        repo.add_snapshot("prep-2", TODAY, progress_percent=99.0)

        trend = await prep_snapshot_service.get_trend(user_id=OWNER, prep_id="prep-1")

        assert len(trend["points"]) == 1
        assert trend["points"][0]["progressPercent"] == 60.0
