"""The Google Calendar connection routes, and keeping a calendar in step with the schedule.

**The client had been calling three endpoints that did not exist.** `CalendarConnectButton` has always
issued `POST /schedule/google-calendar/connect`, `GET .../status` and `POST .../disconnect`; all three
404'd, and because the status check swallows its own errors the button rendered as "not connected" for
ever. Everything underneath was already built — token refresh, calendar creation, event push, recurring
rules — and reachable only from the OAuth callback, which nothing could produce a state for.

Two drift bugs are covered here as well. Only *creating* a block used to sync, so a block moved to a
different hour kept its original time in Google indefinitely, and deleting a block left its event behind
for a session Maigie no longer had.
"""

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.google_calendar.service import google_calendar_service as gcal


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._value if isinstance(self._value, list) else [])


def _session_over(*results):
    """A fake session factory answering `execute` with each supplied result in turn."""
    calls = {"n": 0}
    committed = {"count": 0}

    class _Session:
        async def execute(self, *_a, **_k):
            value = results[calls["n"]] if calls["n"] < len(results) else None
            calls["n"] += 1
            return _Result(value)

        async def commit(self):
            committed["count"] += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    return (lambda: _Session), committed


def _user(**overrides):
    base = {
        "id": "u1",
        "google_calendar_access_token": None,
        "google_calendar_refresh_token": None,
        "google_calendar_token_expires_at": None,
        "google_calendar_sync_enabled": False,
        "google_calendar_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCalendarStatus:
    async def _status(self, user):
        factory, _ = _session_over(user)
        with patch.object(gcal.__class__, "__module__", gcal.__class__.__module__):
            with patch("src.integrations.google_calendar.service.get_session_factory", factory):
                return await gcal.get_status("u1")

    async def test_a_learner_who_never_connected_reads_as_not_connected(self):
        status = await self._status(_user())

        assert status["connected"] is False
        assert status["syncEnabled"] is False
        assert status["calendarId"] is None
        assert status["needsReconnect"] is False

    async def test_a_working_connection_reports_its_calendar(self):
        status = await self._status(
            _user(
                google_calendar_access_token="secret",
                google_calendar_refresh_token="secret-refresh",
                google_calendar_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                google_calendar_sync_enabled=True,
                google_calendar_id="cal-1",
            )
        )

        assert status["connected"] is True
        assert status["syncEnabled"] is True
        assert status["calendarId"] == "cal-1"
        assert status["needsReconnect"] is False

    async def test_an_expired_token_with_no_refresh_asks_for_a_reconnect(self):
        """Connected and useless is its own state. Reporting it as "not connected" would send the learner
        round a flow that appears to succeed and changes nothing."""
        status = await self._status(
            _user(
                google_calendar_access_token="secret",
                google_calendar_refresh_token=None,
                google_calendar_token_expires_at=datetime.now(UTC) - timedelta(hours=1),
                google_calendar_sync_enabled=True,
            )
        )

        assert status["connected"] is True
        assert status["needsReconnect"] is True

    async def test_an_expired_token_that_can_be_refreshed_does_not(self):
        status = await self._status(
            _user(
                google_calendar_access_token="secret",
                google_calendar_refresh_token="secret-refresh",
                google_calendar_token_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )

        assert status["needsReconnect"] is False, "a refresh token means it can renew itself"

    async def test_a_naive_expiry_is_not_compared_against_an_aware_now(self):
        """`googleCalendarTokenExpiresAt` is declared `DateTime(timezone=True)` but lives in a Prisma-era
        table, so it can arrive naive — the mismatch that has already produced four 500s elsewhere."""
        status = await self._status(
            _user(
                google_calendar_access_token="secret",
                google_calendar_token_expires_at=datetime.now() - timedelta(hours=1),  # naive
            )
        )

        assert status["needsReconnect"] is True

    async def test_an_unknown_user_reads_as_not_connected_rather_than_raising(self):
        status = await self._status(None)

        assert status["connected"] is False

    async def test_no_token_reaches_the_response(self):
        """The client needs the state of the connection, never the credential behind it."""
        status = await self._status(
            _user(
                google_calendar_access_token="super-secret-access",
                google_calendar_refresh_token="super-secret-refresh",
            )
        )

        assert "super-secret-access" not in str(status)
        assert "super-secret-refresh" not in str(status)
        assert not [key for key in status if "token" in key.lower()]


class TestCalendarDisconnect:
    async def test_it_clears_the_credentials_and_every_stored_event_id(self):
        """The event ids name events inside a calendar the learner may be about to revoke access to, so
        keeping them would leave the schedule holding references it can no longer reach."""
        user = _user(
            google_calendar_access_token="secret",
            google_calendar_refresh_token="secret-refresh",
            google_calendar_token_expires_at=datetime.now(UTC),
            google_calendar_sync_enabled=True,
            google_calendar_id="cal-1",
        )
        blocks = [
            SimpleNamespace(
                google_calendar_event_id="event-1", google_calendar_synced_at=datetime.now(UTC)
            ),
            SimpleNamespace(
                google_calendar_event_id="event-2", google_calendar_synced_at=datetime.now(UTC)
            ),
        ]
        factory, committed = _session_over(user, blocks)

        with patch("src.integrations.google_calendar.service.get_session_factory", factory):
            await gcal.disconnect("u1")

        assert user.google_calendar_access_token is None
        assert user.google_calendar_refresh_token is None
        assert user.google_calendar_token_expires_at is None
        assert user.google_calendar_sync_enabled is False
        assert user.google_calendar_id is None
        assert all(block.google_calendar_event_id is None for block in blocks)
        assert all(block.google_calendar_synced_at is None for block in blocks)
        assert committed["count"] == 1

    def test_it_does_not_delete_the_events_from_google(self):
        """Disconnecting is not a request to erase what Maigie already wrote — and after the grant is
        revoked it could not be done anyway."""
        source = inspect.getsource(gcal.disconnect)
        body = source.split('"""')[-1]

        assert "delete" not in body.lower()


class TestEventRemoval:
    def test_delete_event_takes_an_event_id_not_a_block_id(self):
        """The caller deletes the block row, so afterwards there is nothing left to read the id from."""
        parameters = list(inspect.signature(gcal.delete_event).parameters)

        assert parameters == ["user_id", "event_id"]

    def test_an_already_deleted_event_counts_as_success(self):
        """`404`/`410` mean the event is gone, which is the state that was wanted."""
        source = inspect.getsource(gcal.delete_event)
        body = source.split('"""')[-1]

        assert "404" in body and "410" in body


class TestScheduleStaysInStepWithTheCalendar:
    """The two drift bugs: an edit that never reached Google, and a deletion that left its event."""

    def test_updating_a_block_re_syncs_it(self):
        from src.domains.progress.services import schedule_service

        source = inspect.getsource(schedule_service.update_block)
        body = source.split('"""')[-1]

        assert "sync_schedule_block" in body

    def test_deleting_a_block_removes_its_event(self):
        from src.domains.progress.services import schedule_service

        source = inspect.getsource(schedule_service.delete_block)

        assert "delete_schedule_block_event" in source

    def test_the_event_id_is_read_before_the_row_is_deleted(self):
        """Reading it afterwards would find nothing, and the event would survive its block for ever."""
        from src.domains.progress.services import schedule_service

        source = inspect.getsource(schedule_service.delete_block)
        read_at = source.index("google_calendar_event_id")
        deleted_at = source.index("progress_repo.delete_block")

        assert read_at < deleted_at

    def test_a_calendar_failure_never_fails_the_local_write(self):
        """Sync is a convenience on top of a schedule that is already saved. Three call sites, each
        tolerant."""
        from src.domains.progress.services import schedule_service

        for function in (
            schedule_service.create_block,
            schedule_service.update_block,
            schedule_service.delete_block,
        ):
            source = inspect.getsource(function)
            assert "except Exception" in source, function.__name__


class TestCalendarRouteOrder:
    def test_the_calendar_routes_are_declared_before_the_block_id_routes(self):
        """FastAPI matches in declaration order. Below `/schedule/{block_id}`, a request for
        `/schedule/google-calendar/status` is read as a block id and 404s with nothing looking wrong —
        the same trap `/goals/summary` had."""
        from src.domains.progress import routes

        source = inspect.getsource(routes)
        calendar_at = source.index('"/schedule/google-calendar/status"')
        block_id_at = source.index('"/schedule/{block_id}"')

        assert calendar_at < block_id_at

    def test_all_three_endpoints_the_client_calls_exist(self):
        from src.main import app

        paths = {getattr(route, "path", "") for route in app.routes}

        assert "/api/v1/progress/schedule/google-calendar/status" in paths
        assert "/api/v1/progress/schedule/google-calendar/connect" in paths
        assert "/api/v1/progress/schedule/google-calendar/disconnect" in paths

    def test_connect_asks_only_for_the_calendars_it_creates(self):
        """`calendar.app.created` and `calendar.freebusy` — enough to manage a calendar Maigie makes, not
        enough to read the learner's existing ones."""
        from src.core.oauth import GoogleOAuthProvider

        source = inspect.getsource(GoogleOAuthProvider.get_authorization_url)
        # Comments as well as the docstring: the code explains *why* it avoids `calendar.events`, so a
        # naive substring check matches the explanation and passes for the wrong reason.
        body = source.split('"""')[-1]
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )

        assert "calendar.app.created" in code
        assert "calendar.events" not in code, "the broader scope would reach existing calendars"

    def test_connect_marks_the_state_so_the_callback_can_recognise_it(self):
        """The callback's `purpose == "calendar_sync"` branch already existed and was unreachable,
        because nothing produced a state carrying that purpose."""
        from src.domains.progress import routes

        source = inspect.getsource(routes.calendar_connect)

        assert '"purpose": "calendar_sync"' in source
        assert '"user_id": current_user.id' in source
        assert "include_calendar=True" in source


class TestPreferredTimesIsTyped:
    """The focus-window distribution is a model, not a `dict`.

    The schedule page renders these buckets, and it has to respect `basis`: `utc_assumed` means the
    learner's timezone was never captured, so the distribution describes the server's day rather than
    theirs and cannot be presented as self-knowledge.
    """

    def test_the_service_shape_validates_against_the_published_model(self):
        """The service builds this dict by hand; if the two drift, the response stops serialising."""
        from src.domains.personal_learning.models import PreferredTimesResponse
        from src.domains.personal_learning.services.behaviour_service import (
            _compute_preferred_times,
        )
        from src.shared.time import UNKNOWN_TIMEZONE

        computed = _compute_preferred_times([9, 10, 14, 22], UNKNOWN_TIMEZONE, 4)
        model = PreferredTimesResponse(**computed)

        assert model.basis == "utc_assumed", "an unknown timezone must say so"
        assert model.timezone is None
        assert model.sessionCount == 4
        assert round(
            model.buckets.morning + model.buckets.afternoon + model.buckets.evening + model.buckets.night
        ) == 100

    def test_a_known_timezone_reports_a_local_basis(self):
        from zoneinfo import ZoneInfo

        from src.domains.personal_learning.models import PreferredTimesResponse
        from src.domains.personal_learning.services.behaviour_service import (
            _compute_preferred_times,
        )
        from src.shared.time import LearnerTimezone

        lagos = LearnerTimezone(
            zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="profile"
        )
        model = PreferredTimesResponse(**_compute_preferred_times([14, 15, 16], lagos, 3))

        assert model.basis == "local"
        assert model.timezone == "Africa/Lagos"
        assert model.buckets.afternoon == 100.0
