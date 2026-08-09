"""Tests for the restored FCM push module.

This was a silent ``pass``, so credit-purchase notifications and the
``notifications.send_push`` Celery task reported success while sending nothing.

The behaviour worth locking down is what happens when things are *not* ideal, since
that is the normal case today: Firebase may be unconfigured, and no endpoint registers
device tokens yet, so there are usually none. None of those situations may raise,
because one caller is a payment fulfilment path.

No test contacts Firebase or the database; both are substituted.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest  # noqa: E402

from src.shared.infrastructure import push_notifications as pn  # noqa: E402


class FakeSendResponse:
    def __init__(self, exception=None):
        self.exception = exception


class FakeBatchResponse:
    def __init__(self, responses):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.exception is None)
        self.failure_count = sum(1 for r in responses if r.exception is not None)


class FakeFcmError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@pytest.fixture
def firebase(monkeypatch):
    """Pretend Firebase is configured."""
    monkeypatch.setattr(pn, "get_firebase_app", lambda: object())


@pytest.fixture
def deleted(monkeypatch):
    """Capture tokens the module tries to delete."""
    captured: list[str] = []

    async def fake_delete(tokens):
        captured.extend(tokens)

    monkeypatch.setattr(pn, "_delete_dead_tokens", fake_delete)
    return captured


def _with_tokens(monkeypatch, tokens):
    async def fake_tokens(user_id):
        return list(tokens)

    monkeypatch.setattr(pn, "_active_tokens_for_user", fake_tokens)


async def test_unconfigured_firebase_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(pn, "get_firebase_app", lambda: None)

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert result["skipped"] is True
    assert result["reason"] == "firebase_not_configured"
    assert result["sent"] == 0


async def test_user_with_no_registered_tokens_is_reported_honestly(firebase, monkeypatch):
    """The normal case today: nothing writes DeviceToken rows yet."""
    _with_tokens(monkeypatch, [])

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert result == {"sent": 0, "failed": 0, "no_tokens": True}


async def test_successful_send_reports_counts(firebase, monkeypatch, deleted):
    _with_tokens(monkeypatch, ["tok-a", "tok-b"])
    monkeypatch.setattr(
        pn.messaging,
        "send_each",
        lambda messages, app=None: FakeBatchResponse([FakeSendResponse(), FakeSendResponse()]),
    )

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert result == {"sent": 2, "failed": 0, "removed_tokens": 0}
    assert deleted == []


@pytest.mark.parametrize(
    "code",
    [
        "UNREGISTERED",
        "NOT_FOUND",
        "INVALID_ARGUMENT",
        "messaging/registration-token-not-registered",
        "messaging/invalid-registration-token",
    ],
)
async def test_permanently_invalid_tokens_are_deleted(firebase, monkeypatch, deleted, code):
    _with_tokens(monkeypatch, ["good", "dead"])
    monkeypatch.setattr(
        pn.messaging,
        "send_each",
        lambda messages, app=None: FakeBatchResponse(
            [FakeSendResponse(), FakeSendResponse(FakeFcmError(code))]
        ),
    )

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert deleted == ["dead"]
    assert result["removed_tokens"] == 1
    assert result["sent"] == 1


async def test_transient_failure_does_not_delete_the_token(firebase, monkeypatch, deleted):
    """A server-side blip must not cost the user their device registration."""
    _with_tokens(monkeypatch, ["tok-a"])
    monkeypatch.setattr(
        pn.messaging,
        "send_each",
        lambda messages, app=None: FakeBatchResponse(
            [FakeSendResponse(FakeFcmError("UNAVAILABLE"))]
        ),
    )

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert deleted == []
    assert result["failed"] == 1
    assert result["removed_tokens"] == 0


async def test_fcm_transport_error_is_contained(firebase, monkeypatch):
    """A payment fulfilment must not fail because FCM is down."""
    _with_tokens(monkeypatch, ["tok-a"])

    def boom(messages, app=None):
        raise RuntimeError("fcm unreachable")

    monkeypatch.setattr(pn.messaging, "send_each", boom)

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert result["error"] == "fcm_send_failed"
    assert result["failed"] == 1


async def test_token_lookup_failure_is_contained(firebase, monkeypatch):
    async def boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(pn, "_active_tokens_for_user", boom)

    result = await pn.send_push_notification("u1", "Title", "Body")

    assert result["error"] == "device_token_lookup_failed"


def test_data_payload_values_are_coerced_to_strings():
    """FCM rejects non-string data values."""
    messages = pn._build_messages(["tok"], "T", "B", {"credits": 500, "ok": True}, None)

    assert messages[0].data == {"credits": "500", "ok": "True"}


async def test_topic_is_added_to_the_data_payload(firebase, monkeypatch):
    captured: dict = {}

    async def fake_send(user_id, title, body, data=None, image_url=None):
        captured.update(data or {})
        return {"sent": 1, "failed": 0}

    monkeypatch.setattr(pn, "send_push_notification", fake_send)

    await pn.send_topic_notification("u1", "schedule_reminder", "T", "B", {"x": "1"})

    assert captured == {"topic": "schedule_reminder", "x": "1"}


async def test_send_push_to_user_delegates(firebase, monkeypatch):
    calls: list[tuple] = []

    async def fake_send(user_id, title, body, data=None, image_url=None):
        calls.append((user_id, title, body, data))
        return {"sent": 1, "failed": 0}

    monkeypatch.setattr(pn, "send_push_notification", fake_send)

    await pn.send_push_to_user("u1", "T", "B", {"k": "v"})

    assert calls == [("u1", "T", "B", {"k": "v"})]


async def test_multiple_users_results_are_aggregated(firebase, monkeypatch):
    async def fake_send(user_id, title, body, data=None, image_url=None):
        return {"sent": 2, "failed": 1}

    monkeypatch.setattr(pn, "send_push_notification", fake_send)

    result = await pn.send_push_to_multiple_users(["u1", "u2", "u3"], "T", "B")

    assert result == {"total_sent": 6, "total_failed": 3, "users": 3}
