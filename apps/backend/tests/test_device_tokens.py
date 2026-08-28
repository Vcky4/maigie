"""Registering a device for push, and the two ways it can leak someone else's notifications.

**The defect this closes.** `DeviceToken` has existed since before this programme, the FCM sender reads it,
builds per-platform payloads and prunes dead tokens — and **nothing has ever written a row**. So every push
the application has ever attempted returned `no_tokens`. Five phases of work whose only route to a learner
is a notification were shipped on top of a channel that reached nobody, and the data said so honestly
(`pushedAt` stays null) which is the only reason it was not worse.

The interesting part is not registration, it is *attribution*. FCM issues one token per app install, so the
same token arrives for different learners as devices change hands, and the row decides who a message is
**sent for**. Both directions of that leak private notifications to the wrong phone:

- A second learner signs in and the token stays on the first: the first learner's notifications are
  delivered to the second learner's device.
- A learner signs out and the token is left behind: same thing, without anyone even signing in.

So the two behaviours pinned hardest here are reassignment on re-register, and removal on sign-out.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402


class FakeSession:
    """An in-memory stand-in for the one table this touches.

    Mirrors the constraint that matters — `token` is `UNIQUE` — because a fake that allows two rows with
    one token would let the reassignment tests pass while production raised.
    """

    def __init__(self, rows: list[Any]):
        self.rows = rows
        self.committed = 0

    async def execute(self, statement):
        text = str(statement)
        if text.startswith("SELECT"):
            token = statement.compile().params.get("token_1")
            match = next((row for row in self.rows if row.token == token), None)
            return SimpleNamespace(
                scalar_one_or_none=lambda: match,
                scalars=lambda: SimpleNamespace(
                    all=lambda: [r.id for r in self.rows if r.user_id == _selected_user(statement)]
                ),
            )
        if text.startswith("DELETE"):
            params = statement.compile().params
            before = len(self.rows)
            self.rows[:] = [
                row
                for row in self.rows
                if not (
                    row.token == params.get("token_1") and row.user_id == params.get("userId_1")
                )
            ]
            return SimpleNamespace(rowcount=before - len(self.rows))
        raise AssertionError(f"unexpected statement: {text[:60]}")

    def add(self, row):
        if any(existing.token == row.token for existing in self.rows):
            raise AssertionError("UNIQUE(token) violated — the real table would refuse this")
        self.rows.append(row)

    async def commit(self):
        self.committed += 1

    async def refresh(self, _row):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _selected_user(statement) -> str | None:
    return statement.compile().params.get("userId_1")


@pytest.fixture
def repo(monkeypatch):
    """The real repository over a fake session, so the SQL it builds is still exercised."""
    from src.domains.identity.repository import identity_repo

    rows: list[Any] = []

    async def _session():
        return FakeSession(rows)

    monkeypatch.setattr(identity_repo, "_get_session", _session)
    return identity_repo, rows


class TestRegistering:
    @pytest.mark.asyncio
    async def test_a_device_is_registered(self, repo):
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="ANDROID"
        )

        assert len(rows) == 1
        assert rows[0].user_id == "learner-1"
        assert rows[0].token == "fcm-abc"
        assert rows[0].platform == "ANDROID"

    @pytest.mark.asyncio
    async def test_registering_twice_does_not_create_a_second_row(self, repo):
        """Clients call this on every launch, which is how the rows appear at all. A second row would
        also violate `UNIQUE(token)` in the real table."""
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="ANDROID"
        )
        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="ANDROID"
        )

        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_a_reinstall_can_change_platform_on_the_same_token(self, repo):
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="ANDROID"
        )
        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="WEB"
        )

        assert len(rows) == 1
        assert rows[0].platform == "WEB"


class TestTheDeviceChangingHands:
    """The reason this is keyed on the token rather than the learner."""

    @pytest.mark.asyncio
    async def test_a_second_learner_on_the_same_device_takes_the_token(self, repo):
        """**The leak.** The row decides who a notification is sent *for*. Left on the first learner, their
        private messages would be delivered to the second learner's phone."""
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="shared-phone", platform="ANDROID"
        )
        await identity_repo.upsert_device_token(
            user_id="learner-2", token="shared-phone", platform="ANDROID"
        )

        assert len(rows) == 1
        assert rows[0].user_id == "learner-2"

    @pytest.mark.asyncio
    async def test_signing_out_removes_the_device(self, repo):
        """The same leak from the other direction, and it needs no one to sign in — a token left behind
        keeps the phone attached to whoever signed out."""
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="IOS"
        )
        removed = await identity_repo.delete_device_token(
            user_id="learner-1", token="fcm-abc"
        )

        assert removed is True
        assert rows == []

    @pytest.mark.asyncio
    async def test_a_token_cannot_be_unregistered_by_someone_else(self, repo):
        """Scoped to the owner, so knowing the string is not enough to silence another learner's device."""
        identity_repo, rows = repo

        await identity_repo.upsert_device_token(
            user_id="learner-1", token="fcm-abc", platform="IOS"
        )
        removed = await identity_repo.delete_device_token(
            user_id="learner-2", token="fcm-abc"
        )

        assert removed is False
        assert len(rows) == 1
        assert rows[0].user_id == "learner-1"

    @pytest.mark.asyncio
    async def test_unregistering_something_absent_is_not_an_error(self, repo):
        """Sign-out is idempotent. Reporting a failure for an already-unregistered device would make a
        correct sign-out look broken."""
        identity_repo, _ = repo

        assert await identity_repo.delete_device_token(user_id="l1", token="never") is False


class TestTheContract:
    def test_the_platforms_are_a_closed_set(self):
        """The column has no CHECK, and the sender builds per-platform payloads — a value it does not
        recognise is a device that silently receives nothing."""
        from typing import get_args

        from src.domains.identity.models import DeviceTokenRequest

        field = DeviceTokenRequest.model_fields["platform"]
        assert set(get_args(field.annotation)) == {"ANDROID", "IOS", "WEB"}

    def test_registration_is_idempotent_by_method(self):
        """`PUT`, not `POST`: registering the same device twice is not creating a second one, and clients
        are expected to call it on every launch."""
        from src.main import app

        methods = {
            method
            for route in app.routes
            if getattr(route, "path", "") == "/api/v1/users/me/device-tokens"
            for method in route.methods
        }
        assert methods == {"PUT", "DELETE"}

    def test_the_sender_reads_what_registration_writes(self):
        """Both sides address the same table. The sender was complete for the whole time nothing wrote to
        it, so this pins that the two halves actually meet."""
        from src.domains.identity.db_models import DeviceToken
        from src.shared.infrastructure import push_notifications

        assert push_notifications.DeviceToken is DeviceToken
