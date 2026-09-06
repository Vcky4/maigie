"""Unsubscribe links that work from an inbox, and provider events that cannot be forged.

Both halves protect something a learner cannot see and cannot appeal.

An unsubscribe link is followed without a session, often by a mail provider's own machinery
rather than a person. If it requires auth, or asks for a confirmation, or quietly fails, the
provider concludes Maigie does not honour unsubscribes — and the learner's next move is the spam
button, which is the same signal with a permanent reputational cost attached. So the token has
to carry its own proof, and the endpoint has to act.

A webhook endpoint is public. A forged `delivered` would launder a failure into a success in the
ledger; a forged `bounced` would suppress someone's address on an attacker's say-so. And because
providers retry without promising ordering, a replayed `bounced` must not suppress twice and a
late `delivered` must not overwrite a real failure.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.notifications import email_webhooks, unsubscribe  # noqa: E402
from src.domains.notifications.email_delivery import address_reference  # noqa: E402

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key-32-bytes-long").decode()


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"RESEND_WEBHOOK_SECRET": SECRET}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _signed(payload: dict[str, Any], *, event_id: str = "msg_1") -> dict[str, Any]:
    """Build a correctly signed Svix-style request for ``payload``."""

    body = json.dumps(payload).encode()
    timestamp = "1756700000"
    key = base64.b64decode(SECRET.split("_", 1)[1])
    signed = f"{event_id}.{timestamp}.{body.decode()}".encode()
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "body": body,
        "svix_id": event_id,
        "svix_timestamp": timestamp,
        "svix_signature": f"v1,{signature}",
        "payload": payload,
    }


def _event(event_type: str, **data: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": event_type,
        "created_at": "2026-09-01T10:00:00.000Z",
        "data": {"email_id": "prov-1", "to": ["learner@example.com"], **data},
    }
    return payload


# ---------------------------------------------------------------------------
# Unsubscribe tokens
# ---------------------------------------------------------------------------


class TestUnsubscribeToken:
    def test_a_token_round_trips_its_user_and_scope(self) -> None:
        token = unsubscribe.create_unsubscribe_token("user-1", "PROGRESS")

        parsed = unsubscribe.parse_unsubscribe_token(token)
        assert parsed is not None
        assert (parsed.user_id, parsed.scope) == ("user-1", "PROGRESS")

    def test_a_tampered_token_is_refused(self) -> None:
        token = unsubscribe.create_unsubscribe_token("user-1", "ALL")
        payload, _, signature = token.partition(".")

        # Someone editing the payload to unsubscribe a different learner.
        forged_payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"v": 1, "u": "user-2", "s": "ALL"}, separators=(",", ":"), sort_keys=True
                ).encode()
            )
            .decode()
            .rstrip("=")
        )

        assert unsubscribe.parse_unsubscribe_token(f"{forged_payload}.{signature}") is None
        assert unsubscribe.parse_unsubscribe_token(f"{payload}.{signature[:-2]}xx") is None

    @pytest.mark.parametrize("token", ["", "nodot", "a.b", "!!!.???", "."])
    def test_malformed_tokens_return_none_rather_than_raising(self, token: str) -> None:
        # A truncated URL from a mail client is normal input, not an exceptional condition.
        assert unsubscribe.parse_unsubscribe_token(token) is None

    def test_an_unknown_scope_cannot_be_minted(self) -> None:
        with pytest.raises(ValueError):
            unsubscribe.create_unsubscribe_token("user-1", "BILLING")  # type: ignore[arg-type]

    def test_a_token_signed_with_another_secret_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = unsubscribe.create_unsubscribe_token("user-1", "ALL")

        # Rotating SECRET_KEY invalidates outstanding links, which is the intended trade: the
        # footer link is regenerated on every send.
        monkeypatch.setattr(
            unsubscribe, "get_settings", lambda: Settings(_env_file=None, SECRET_KEY="rotated")
        )
        assert unsubscribe.parse_unsubscribe_token(token) is None


# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------


@dataclass
class FakeRepo:
    recorded: list[dict[str, Any]] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    suppressed: list[tuple[str, str]] = field(default_factory=list)
    already_ingested: bool = False

    async def record_email_provider_event(self, **values: Any) -> bool:
        self.recorded.append(values)
        return not self.already_ingested

    async def mark_email_delivered(self, *, provider_message_id: str, delivered_at: Any) -> bool:
        self.delivered.append(provider_message_id)
        return True

    async def mark_email_failed(
        self, *, provider_message_id: str, failure_code: str, failed_at: Any
    ) -> bool:
        self.failed.append((provider_message_id, failure_code))
        return True

    async def suppress_address(self, address_hash: str, *, reason: str, **_: Any) -> bool:
        self.suppressed.append((address_hash, reason))
        return True


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(email_webhooks, "notification_repo", fake)
    monkeypatch.setattr(email_webhooks, "get_settings", _settings)
    return fake


class TestSignatureVerification:
    @pytest.mark.asyncio
    async def test_a_correctly_signed_event_is_accepted(self, repo: FakeRepo) -> None:
        result = await email_webhooks.process_resend_event(**_signed(_event("email.delivered")))

        assert result.accepted is True
        assert result.outcome == "DELIVERED"

    @pytest.mark.asyncio
    async def test_a_body_altered_after_signing_is_rejected(self, repo: FakeRepo) -> None:
        request = _signed(_event("email.delivered"))
        # The signature covers the body, so swapping the event type must invalidate it. This is
        # the forgery that would launder a bounce into a delivery.
        request["body"] = json.dumps(_event("email.bounced")).encode()

        result = await email_webhooks.process_resend_event(**request)

        assert result.accepted is False
        assert result.outcome == "INVALID_SIGNATURE"
        assert repo.recorded == []

    @pytest.mark.asyncio
    async def test_an_unconfigured_secret_refuses_everything(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            email_webhooks, "get_settings", lambda: _settings(RESEND_WEBHOOK_SECRET="")
        )

        result = await email_webhooks.process_resend_event(**_signed(_event("email.delivered")))

        # Fail closed: an unconfigured deployment must not be feedable.
        assert result.accepted is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header", ["", "v1,not-a-signature", "v2,abc"])
    async def test_missing_or_wrong_version_signatures_are_rejected(
        self, repo: FakeRepo, header: str
    ) -> None:
        request = _signed(_event("email.delivered"))
        request["svix_signature"] = header

        assert (await email_webhooks.process_resend_event(**request)).accepted is False

    @pytest.mark.asyncio
    async def test_a_rotating_secret_header_with_several_signatures_still_verifies(
        self, repo: FakeRepo
    ) -> None:
        request = _signed(_event("email.delivered"))
        # During a rotation the provider sends every candidate, space separated.
        request["svix_signature"] = f"v1,old-signature {request['svix_signature']}"

        assert (await email_webhooks.process_resend_event(**request)).accepted is True


class TestEventEffects:
    @pytest.mark.asyncio
    async def test_delivery_is_only_claimed_on_the_providers_word(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(**_signed(_event("email.delivered")))

        assert repo.delivered == ["prov-1"]
        assert repo.suppressed == []

    @pytest.mark.asyncio
    async def test_a_hard_bounce_suppresses_the_address(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(
            **_signed(_event("email.bounced", bounce={"type": "Permanent"}))
        )

        assert repo.failed == [("prov-1", "HARD_BOUNCE")]
        assert repo.suppressed == [(address_reference("learner@example.com"), "HARD_BOUNCE")]

    @pytest.mark.asyncio
    async def test_a_soft_bounce_fails_the_attempt_but_spares_the_address(
        self, repo: FakeRepo
    ) -> None:
        await email_webhooks.process_resend_event(
            **_signed(_event("email.bounced", bounce={"type": "Transient"}))
        )

        assert repo.failed == [("prov-1", "SOFT_BOUNCE")]
        # A full mailbox works again tomorrow; suppressing would cut the learner off for good.
        assert repo.suppressed == []

    @pytest.mark.asyncio
    async def test_an_unlabelled_bounce_is_treated_as_soft(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(**_signed(_event("email.bounced")))

        # A wrong suppression is invisible and permanent; a wrong retry is bounded by the cap.
        assert repo.suppressed == []

    @pytest.mark.asyncio
    async def test_a_complaint_suppresses_the_address(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(**_signed(_event("email.complained")))

        assert repo.suppressed == [(address_reference("learner@example.com"), "COMPLAINT")]

    @pytest.mark.asyncio
    async def test_a_delay_changes_nothing_but_is_recorded(self, repo: FakeRepo) -> None:
        result = await email_webhooks.process_resend_event(
            **_signed(_event("email.delivery_delayed"))
        )

        assert result.outcome == "DELAYED"
        assert (repo.delivered, repo.failed, repo.suppressed) == ([], [], [])
        assert repo.recorded[0]["outcome"] == "DELAYED"

    @pytest.mark.asyncio
    async def test_an_unknown_event_type_is_recorded_rather_than_dropped(
        self, repo: FakeRepo
    ) -> None:
        result = await email_webhooks.process_resend_event(**_signed(_event("email.opened")))

        # Accepted so the provider stops retrying, recorded so a new event type is visible.
        assert result.accepted is True
        assert result.outcome == "IGNORED"
        assert repo.recorded[0]["event_type"] == "email.opened"


class TestReplaySafety:
    @pytest.mark.asyncio
    async def test_a_replayed_event_applies_nothing_a_second_time(self, repo: FakeRepo) -> None:
        repo.already_ingested = True

        result = await email_webhooks.process_resend_event(
            **_signed(_event("email.bounced", bounce={"type": "Permanent"}))
        )

        # Accepted, so the provider stops retrying a settled event — but no second suppression.
        assert (result.accepted, result.outcome) == (True, "REPLAY")
        assert repo.suppressed == []
        assert repo.failed == []

    @pytest.mark.asyncio
    async def test_the_providers_event_id_is_what_makes_it_idempotent(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(
            **_signed(_event("email.delivered"), event_id="evt_abc")
        )

        assert repo.recorded[0]["provider_event_id"] == "evt_abc"
        assert repo.recorded[0]["provider"] == "resend"


class TestRecordedEventShape:
    @pytest.mark.asyncio
    async def test_the_address_is_recorded_as_a_hash_not_an_address(self, repo: FakeRepo) -> None:
        await email_webhooks.process_resend_event(**_signed(_event("email.delivered")))

        recorded = repo.recorded[0]
        assert recorded["address_hash"] == address_reference("learner@example.com")
        # `default=str` because the record carries a datetime; the point of the check is that
        # no plaintext address reaches the row, whatever its other field types are.
        assert "learner@example.com" not in json.dumps(recorded, default=str)

    @pytest.mark.asyncio
    async def test_an_unparseable_timestamp_does_not_lose_the_event(self, repo: FakeRepo) -> None:
        payload = _event("email.delivered")
        payload["created_at"] = "not a date"

        result = await email_webhooks.process_resend_event(**_signed(payload))

        assert result.accepted is True
        assert isinstance(repo.recorded[0]["occurred_at"], datetime)
        assert repo.recorded[0]["occurred_at"].tzinfo is UTC
