"""`GoalProgressSnapshot` — the day it dates a row to, and the honesty of an empty series.

The table exists because `Goal.progress` is overwritten in place, so a trajectory is not derivable from
the tables that exist. It is the third snapshot table in this codebase and the **first that cannot be
backfilled**: mastery was reconstructed from `Topic.completedAt`, and a goal's progress leaves no dated
trail to replay. Decision Y therefore accepts an empty chart that says it is building, and these tests
pin the two things that would quietly undo that — a row dated to the wrong day, and an empty series
that cannot be told apart from a missing feature.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from src.domains.progress.services import goal_snapshot_service  # noqa: E402
from src.shared.time import LearnerTimezone  # noqa: E402

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
)
LOS_ANGELES = LearnerTimezone(
    zone=ZoneInfo("America/Los_Angeles"),
    name="America/Los_Angeles",
    is_known=True,
    source="DEVICE",
)
UNKNOWN = LearnerTimezone(zone=ZoneInfo("UTC"), name="UTC", is_known=False, source=None)


def _goal(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "goal-1",
        "user_id": "u",
        "progress": 40.0,
        "status": "ACTIVE",
        "metric_kind": "manual",
        "current_value": 12.0,
        "course_id": None,
        "topic_id": None,
        "prep_id": None,
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
        "target_date": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _rows_session(*, rows: list, added: list, executed: list | None = None):
    """An async-context session whose one query returns `rows` and whose `add` appends to `added`.

    `executed` records every statement, which is how the write-back onto `Goal.progress` is observed.
    """

    class _Scalars:
        def all(self):
            return rows

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, *args, **_k):
            if executed is not None:
                executed.append(args)
            return _Result()

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    return _Session()


async def _capture(*, timezone_, now, goals, existing=None, measured_value=99.0):
    """Run `capture_for_users` with the database and the measurement step stubbed out.

    Returns the rows the writer would have inserted, plus the statements it executed.
    """
    added: list = []
    executed: list = []

    async def _fake_derive(goal_list, *, now=None):
        from src.domains.progress.services.goal_metrics import GoalMeasurement

        return {
            g.id: GoalMeasurement(current_value=measured_value, measured=True) for g in goal_list
        }

    calls = {"n": 0}

    def _factory():
        # The first session loads the goals; the second reads the day's existing rows and writes.
        calls["n"] += 1
        if calls["n"] == 1:
            return _rows_session(rows=goals, added=added)
        return _rows_session(rows=existing or [], added=added, executed=executed)

    with (
        patch.object(goal_snapshot_service, "get_session_factory", lambda: _factory),
        patch.object(goal_snapshot_service, "resolve_many", _stub_resolve(timezone_)),
        patch.object(goal_snapshot_service.goal_metrics, "derive_current_values", _fake_derive),
    ):
        await goal_snapshot_service.capture_for_users(["u"], now=now)

    #: Statements carrying a list of `{id, progress}` payloads — the write-back onto `Goal.progress`.
    write_backs = [
        args[1]
        for args in executed
        if len(args) > 1 and isinstance(args[1], list) and args[1] and isinstance(args[1][0], dict)
    ]
    return SimpleNamespace(added=added, write_backs=write_backs)


def _stub_resolve(timezone_):
    async def _resolve(user_ids):
        return {user_id: timezone_ for user_id in user_ids}

    return _resolve


class TestTheDayARowIsDatedTo:
    """The row must land on the learner's last finished local day.

    Same convention as `DailyLearningSnapshot`, so both tables share an x-axis. A UTC date would put a
    goal edited at 23:30 in Lagos on the wrong day — `PrepReadinessSnapshot` does exactly that and its
    own docstring records it as a bug.
    """

    async def test_records_the_previous_local_day(self):
        # 01:30 UTC on 24 August is 02:30 on the 24th in Lagos, so the finished day is the 23rd.
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal()],
        )

        assert len(recorder.added) == 1
        assert recorder.added[0].captured_on == date(2026, 8, 23)

    async def test_a_learner_west_of_utc_gets_their_own_day_not_the_utc_one(self):
        """01:30 UTC on the 24th is still 18:30 on the **23rd** in Los Angeles, so their last
        finished day is the 22nd — not the 23rd a UTC clock would have written."""
        recorder = await _capture(
            timezone_=LOS_ANGELES,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal()],
        )

        assert recorder.added[0].captured_on == date(2026, 8, 22)

    async def test_an_unknown_timezone_falls_back_to_utc_without_claiming_to_know(self):
        recorder = await _capture(
            timezone_=UNKNOWN,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal()],
        )

        assert recorder.added[0].captured_on == date(2026, 8, 23)


class TestWhatIsRecorded:
    async def test_a_measured_value_is_flagged_as_measured(self):
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal(metric_kind="focused_minutes")],
        )
        row = recorder.added[0]

        assert row.current_value == 99.0
        assert row.current_value_measured is True

    async def test_progress_and_status_come_from_the_goal(self):
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal(progress=72.5, status="COMPLETED")],
        )
        row = recorder.added[0]

        assert row.progress == 72.5
        assert row.status == "COMPLETED"

    async def test_a_second_run_on_the_same_day_updates_rather_than_duplicating(self):
        """Idempotency is what makes a retry, an overlapping run, or a manual re-run safe. The unique
        index enforces it in the database; this is the writer choosing update over insert."""
        existing = SimpleNamespace(
            goal_id="goal-1",
            progress=10.0,
            current_value=1.0,
            current_value_measured=False,
            status="ACTIVE",
        )
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal(progress=55.0)],
            existing=[existing],
        )

        assert recorder.added == [], "a row was inserted for a day that already had one"
        assert existing.progress == 55.0
        assert existing.current_value == 99.0
        assert existing.current_value_measured is True

    async def test_a_measured_goals_progress_is_derived_not_read_from_the_column(self):
        """`Goal.progress` is a column nothing writes. Recording it would give the trajectory table a
        flat line for a goal that was moving."""
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[
                _goal(
                    progress=0.0, metric_kind="course_progress", course_id="c1", target_value=100.0
                )
            ],
            measured_value=64.0,
        )

        assert recorder.added[0].progress == 64.0


class TestTheStoredColumnIsBroughtUpToDate:
    """The nightly write-back. Every reader that matters derives progress now, so this is not what
    makes the API correct — it is what stops `Goal.progress` being a lie for an export, a migration or
    a hand-written query. `update_progress` was its only writer and has never been called."""

    async def test_a_drifted_measured_goal_is_written_back(self):
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[
                _goal(
                    progress=0.0, metric_kind="course_progress", course_id="c1", target_value=100.0
                )
            ],
            measured_value=64.0,
        )

        assert recorder.write_backs == [[{"id": "goal-1", "progress": 64.0}]]

    async def test_a_manual_goal_is_never_written_back(self):
        """Its figure is the learner's own. Overwriting it would discard what they typed."""
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[_goal(progress=20.0, metric_kind="manual")],
            measured_value=64.0,
        )

        assert recorder.write_backs == []

    async def test_an_unchanged_figure_is_not_rewritten(self):
        """Writing an unchanged value would move `updatedAt` on every goal every night, which would
        make "recently changed" meaningless."""
        recorder = await _capture(
            timezone_=LAGOS,
            now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC),
            goals=[
                _goal(
                    progress=64.0, metric_kind="course_progress", course_id="c1", target_value=100.0
                )
            ],
            measured_value=64.0,
        )

        assert recorder.write_backs == []

    async def test_no_goals_writes_nothing(self):
        recorder = await _capture(
            timezone_=LAGOS, now=datetime(2026, 8, 24, 1, 30, tzinfo=UTC), goals=[]
        )

        assert recorder.added == []


class TestOnlyGoalsWorthAHistory:
    def test_archived_and_cancelled_are_not_tracked(self):
        """A goal the learner stopped keeps drawing a flat line otherwise — volume with no
        information. Completed goals *are* tracked, so the chart shows where the line reached 100
        rather than stopping short of it."""
        assert goal_snapshot_service._TRACKED_STATUSES == ("ACTIVE", "COMPLETED")
        assert "ARCHIVED" not in goal_snapshot_service._TRACKED_STATUSES
        assert "CANCELLED" not in goal_snapshot_service._TRACKED_STATUSES


class TestNoReconstruction:
    def test_the_module_offers_no_backfill(self):
        """Deliberate, and the difference from the other two snapshot tables.

        `daily_snapshot_service` has a companion backfill because mastery could be reconstructed from
        `Topic.completedAt`. A goal's progress has no dated event trail, so a backfill could only
        interpolate — a straight line presented as measurement, which is the defect this programme
        exists to close. If a `backfill` appears here, it is either reading a source that did not
        exist when this was written or it is inventing data.
        """
        assert not hasattr(goal_snapshot_service, "backfill")
        assert not any(name.startswith("backfill") for name in dir(goal_snapshot_service))


class TestHistoryWindowEndsYesterday:
    def test_the_route_asks_for_the_newest_day_that_can_exist(self):
        """The writer records the last *finished* day, so a window ending today asks for a row that by
        definition does not exist and reports one fewer captured day than it has.
        `growth_service._window` learned this the same way."""
        import inspect

        from src.domains.progress import routes

        source = inspect.getsource(routes.get_goal_history)
        assert "timedelta(days=1)" in source, "the window no longer steps back off today"
        assert "days - 1" in source


class TestMigrationRevisionIds:
    def test_no_revision_id_exceeds_the_alembic_version_column(self):
        """`alembic_version.version_num` is `varchar(32)` in this database.

        A longer id applies its DDL and then fails on the version bump, so the whole transaction rolls
        back and the only symptom is a `StringDataRightTruncationError` about a value nobody wrote —
        which is a confusing way to learn that a filename is two characters too long. The longest id in
        the tree was exactly 32 when this was added, so the ceiling had never been reached.
        """
        import pathlib
        import re

        versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
        offenders = {}
        for path in versions.glob("*.py"):
            match = re.search(r'^revision = "(.+?)"', path.read_text(), re.M)
            if match and len(match.group(1)) > 32:
                offenders[path.name] = len(match.group(1))

        assert not offenders, f"revision ids longer than 32 characters: {offenders}"
