"""Consent, safety, and evidence for web push.

Three failure modes are worth guarding here, and they are not the ones email has.

*The endpoint is a URL the caller chooses.* A background worker POSTs to it, so a missing check
turns the notification system into a request forwarder aimed at anything the worker can reach.
These tests assert the refusals, not just the acceptances, because an SSRF hole is invisible
until it is used.

*The subscription is sending authority.* Endpoint plus `p256dh` plus `auth` is everything needed
to push arbitrary messages to a learner's browser, since push services do not authenticate
senders. So the two secrets are encrypted at rest, and that has to actually round-trip.

*A dead subscription is dead permanently.* A push service answering 410 is authoritative in a
way no email bounce is. Retrying it forever would produce a queue that never drains and a
failure rate that never recovers, so expiry must prune rather than count a failure.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import http_ece
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt
from pydantic import ValidationError

from src.config import Settings
from src.domains.notifications import (
    subscription_crypto,
    web_push_delivery,
    web_push_dispatcher,
    web_push_endpoint,
)
from src.domains.notifications.models import WebPushSubscriptionUpsert
from src.domains.notifications.subscription_crypto import (
    SubscriptionSecretUnreadable,
    decrypt_subscription_secret,
    encrypt_subscription_secret,
)
from src.domains.notifications.web_push_delivery import (
    build_payload,
    encrypt_payload,
    send_web_push,
    vapid_public_key,
    web_push_configured,
)
from src.domains.notifications.web_push_endpoint import (
    InvalidPushSubscription,
    validate_auth,
    validate_p256dh,
    validate_push_endpoint,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
CHROME = "https://fcm.googleapis.com/fcm/send/cJKl9"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ---- a browser subscription and a server identity, generated once ---------------------------
_CLIENT_KEY = ec.generate_private_key(ec.SECP256R1())
_AUTH_SECRET = os.urandom(16)
P256DH = _b64(
    _CLIENT_KEY.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
)
AUTH = _b64(_AUTH_SECRET)

_VAPID_KEY = ec.generate_private_key(ec.SECP256R1())
VAPID_PUBLIC = _b64(
    _VAPID_KEY.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
)
VAPID_PRIVATE = _b64(_VAPID_KEY.private_numbers().private_value.to_bytes(32, "big"))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "WEB_PUSH_ENABLED": True,
        "WEB_PUSH_ROLLOUT_PERCENT": 100,
        "WEB_PUSH_VAPID_PUBLIC_KEY": VAPID_PUBLIC,
        "WEB_PUSH_VAPID_PRIVATE_KEY": VAPID_PRIVATE,
        "WEB_PUSH_VAPID_SUBJECT": "mailto:support@maigie.com",
        "SECRET_KEY": "test-secret-key-for-web-push-tests",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _patch_settings(monkeypatch: pytest.MonkeyPatch, configured: Settings) -> None:
    """Point every module that reads configuration at `configured`.

    Each of these does `from src.config import ...` at import time, so it holds its own
    reference and patching `src.config` alone would leave them on the real settings — which
    silently turns an assertion about a refusal into an assertion about nothing.
    """

    monkeypatch.setattr(web_push_delivery, "settings", configured)
    monkeypatch.setattr(web_push_endpoint, "get_settings", lambda: configured)
    monkeypatch.setattr(subscription_crypto, "get_settings", lambda: configured)
    monkeypatch.setattr("src.config.get_settings", lambda: configured)


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Every test runs against a configured sender unless it overrides this."""
    configured = _settings()
    _patch_settings(monkeypatch, configured)
    web_push_delivery._token_cache.clear()
    return configured


class TestEndpointSafety:
    """The endpoint is attacker-controlled input that a worker will POST to."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            CHROME,
            "https://android.googleapis.com/gcm/send/abc",
            "https://updates.push.services.mozilla.com/wpush/v2/gAAA",
            "https://web.push.apple.com/QJ8v",
            "https://wns2-by3p.notify.windows.com/w/?token=AQ",
        ],
    )
    def test_accepts_every_push_service_phase_4_targets(self, endpoint: str) -> None:
        assert validate_push_endpoint(endpoint) == endpoint

    @pytest.mark.parametrize(
        ("label", "endpoint"),
        [
            ("cloud metadata", "https://169.254.169.254/latest/meta-data/"),
            ("loopback", "https://127.0.0.1/push"),
            ("private range", "https://10.0.0.5/push"),
            ("internal service name", "https://notifications.svc.cluster.local/push"),
            ("plain http", "http://fcm.googleapis.com/fcm/send/x"),
            ("file scheme", "file:///etc/passwd"),
            ("no host", "https:///push"),
        ],
    )
    def test_refuses_anything_that_is_not_a_push_service(self, label: str, endpoint: str) -> None:
        with pytest.raises(InvalidPushSubscription):
            validate_push_endpoint(endpoint)

    def test_refuses_a_suffix_near_miss(self) -> None:
        """`.notify.windows.com` must match a label boundary, not any string ending in it."""
        with pytest.raises(InvalidPushSubscription):
            validate_push_endpoint("https://evilnotify.windows.com/w/")

    def test_refuses_credentials_that_disguise_the_real_host(self) -> None:
        """`https://allowed@internal/` reads as the allowed host to a careless parser."""
        with pytest.raises(InvalidPushSubscription):
            validate_push_endpoint("https://fcm.googleapis.com@internal.host/push")

    def test_refuses_a_custom_port_on_an_allowed_host(self) -> None:
        with pytest.raises(InvalidPushSubscription):
            validate_push_endpoint("https://fcm.googleapis.com:8080/fcm/send/x")

    def test_an_allowlist_change_takes_effect_without_a_deploy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_settings(
            monkeypatch, _settings(WEB_PUSH_ALLOWED_ENDPOINT_HOSTS=["push.example.test"])
        )
        assert validate_push_endpoint("https://push.example.test/x")
        with pytest.raises(InvalidPushSubscription):
            validate_push_endpoint(CHROME)


class TestKeyMaterialValidation:
    def test_accepts_a_real_subscription(self) -> None:
        assert validate_p256dh(P256DH) == P256DH
        assert validate_auth(AUTH) == AUTH

    def test_refuses_a_point_that_is_not_on_the_curve(self) -> None:
        """Encrypting to it would fail later, and a bad point is how curve attacks start."""
        with pytest.raises(InvalidPushSubscription):
            validate_p256dh(_b64(b"\x04" + b"\x01" * 64))

    @pytest.mark.parametrize("size", [8, 15, 17, 32])
    def test_refuses_an_auth_secret_that_is_not_sixteen_bytes(self, size: int) -> None:
        with pytest.raises(InvalidPushSubscription):
            validate_auth(_b64(os.urandom(size)))

    def test_the_api_model_rejects_before_anything_is_stored(self) -> None:
        with pytest.raises(ValidationError):
            WebPushSubscriptionUpsert.model_validate(
                {
                    "installationId": "web-1",
                    "endpoint": "https://127.0.0.1/push",
                    "p256dh": P256DH,
                    "auth": AUTH,
                }
            )

    def test_the_api_model_does_not_accept_a_claimed_permission_state(self) -> None:
        """A subscription's existence is the grant; a client cannot assert consent."""
        model = WebPushSubscriptionUpsert.model_validate(
            {
                "installationId": "web-1",
                "endpoint": CHROME,
                "p256dh": P256DH,
                "auth": AUTH,
                "permissionState": "GRANTED",
            }
        )
        assert not hasattr(model, "permission_state")


class TestSubscriptionSecretsAtRest:
    def test_round_trips_through_the_stored_form(self) -> None:
        sealed = encrypt_subscription_secret(AUTH)
        assert sealed.startswith("v1.") and AUTH not in sealed
        assert decrypt_subscription_secret(sealed) == AUTH

    def test_the_same_value_encrypts_differently_each_time(self) -> None:
        assert encrypt_subscription_secret(AUTH) != encrypt_subscription_secret(AUTH)

    @pytest.mark.parametrize("stored", ["", "v9.abcd", "v1.AAAA", "not-even-close"])
    def test_refuses_an_unusable_stored_value(self, stored: str) -> None:
        with pytest.raises(SubscriptionSecretUnreadable):
            decrypt_subscription_secret(stored)

    def test_refuses_a_tampered_ciphertext(self) -> None:
        sealed = encrypt_subscription_secret(AUTH)
        tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
        with pytest.raises(SubscriptionSecretUnreadable):
            decrypt_subscription_secret(tampered)

    def test_a_rotated_secret_key_makes_rows_unreadable_rather_than_wrong(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dispatcher turns this into a prune, so the learner is asked to resubscribe."""
        sealed = encrypt_subscription_secret(AUTH)
        _patch_settings(monkeypatch, _settings(SECRET_KEY="a-different-secret-key"))
        with pytest.raises(SubscriptionSecretUnreadable):
            decrypt_subscription_secret(sealed)


class TestPayload:
    def test_a_browser_can_decrypt_what_we_send(self) -> None:
        """The whole channel is worthless if the ciphertext is not what a browser expects."""
        payload = build_payload(
            notification_id="n1",
            title="Time to study",
            body="Your Biology session starts in 15 minutes",
            action={"kind": "OPEN_SESSION", "v": 1},
            category="LEARNING",
        )
        opened = http_ece.decrypt(
            encrypt_payload(payload, p256dh=P256DH, auth=AUTH),
            private_key=_CLIENT_KEY,
            auth_secret=_AUTH_SECRET,
            version="aes128gcm",
        )
        assert opened == payload

    def test_carries_the_canonical_action_and_never_a_url(self) -> None:
        """The client owns route mapping; a server URL would be a second routing authority."""
        payload = build_payload(
            notification_id="n1",
            title="t",
            body="b",
            action={"kind": "OPEN_SESSION", "v": 1},
            category="LEARNING",
        )
        assert b'"kind":"OPEN_SESSION"' in payload
        assert b"http" not in payload

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            ("ascii", "x" * 9000),
            ("accented", "é" * 5000),
            ("emoji", "📚" * 3000),
            ("quotes and slashes", '"\\' * 4000),
            ("newlines", "line\n" * 2000),
        ],
    )
    def test_an_over_long_body_is_trimmed_to_fit_every_encoding(
        self, label: str, body: str
    ) -> None:
        """Subtracting a byte overflow from a character count empties a multibyte body."""
        payload = build_payload(
            notification_id="n1", title="Long", body=body, action=None, category="LEARNING"
        )
        assert len(payload) <= web_push_delivery.MAX_PAYLOAD_BYTES
        assert len(encrypt_payload(payload, p256dh=P256DH, auth=AUTH)) <= 4096
        # Trimmed, not emptied: a message with no body is not worth interrupting for.
        assert len(payload) > web_push_delivery.MAX_PAYLOAD_BYTES // 2

    def test_a_body_that_already_fits_is_left_exactly_alone(self) -> None:
        payload = build_payload(
            notification_id="n1", title="t", body="short body", action=None, category="LEARNING"
        )
        assert b'"body":"short body"' in payload


class TestVapid:
    def test_reports_unconfigured_rather_than_signing_with_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(web_push_delivery, "settings", _settings(WEB_PUSH_VAPID_PRIVATE_KEY=""))
        assert not web_push_configured()

    def test_reports_unconfigured_for_a_malformed_private_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            web_push_delivery, "settings", _settings(WEB_PUSH_VAPID_PRIVATE_KEY="not-base64url!!")
        )
        assert not web_push_configured()

    def test_publishes_the_key_a_browser_must_subscribe_with(self) -> None:
        assert vapid_public_key() == VAPID_PUBLIC
        assert len(base64.urlsafe_b64decode(VAPID_PUBLIC + "==")) == 65

    def test_the_audience_is_the_origin_without_the_path(self) -> None:
        """Including the path is a common mistake that presents as an unexplained 401."""
        header = web_push_delivery._vapid_headers(CHROME)["Authorization"]
        token = header.split("t=", 1)[1].split(",", 1)[0]
        claims = jwt.decode(
            token,
            _VAPID_KEY.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode(),
            algorithms=["ES256"],
            audience="https://fcm.googleapis.com",
        )
        assert claims["aud"] == "https://fcm.googleapis.com"
        assert claims["sub"] == "mailto:support@maigie.com"

    def test_reuses_one_token_across_a_batch_to_the_same_service(self) -> None:
        first = web_push_delivery._vapid_headers(CHROME)
        second = web_push_delivery._vapid_headers(CHROME + "/other")
        assert first == second

    def test_signs_separately_per_push_service(self) -> None:
        assert web_push_delivery._vapid_headers(CHROME) != web_push_delivery._vapid_headers(
            "https://web.push.apple.com/QJ8v"
        )


def _responder(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Route the sender's HTTP through a mock transport, capturing requests."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(web_push_delivery.httpx, "AsyncClient", factory)
    return seen


class TestStatusClassification:
    """The status code is the only signal about a subscription's health."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 201, 202])
    async def test_acceptance_is_terminal_and_carries_the_message_id(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _responder(
            monkeypatch,
            lambda r: httpx.Response(status, headers={"location": "https://push/m/1"}),
        )
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.accepted and not outcome.retryable and not outcome.expired
        assert outcome.provider_message_id == "https://push/m/1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 410])
    async def test_gone_means_prune_not_retry(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _responder(monkeypatch, lambda r: httpx.Response(status))
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.expired and not outcome.retryable and not outcome.accepted

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_rate_limits_and_outages_are_retryable(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _responder(monkeypatch, lambda r: httpx.Response(status))
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.retryable and not outcome.expired

    @pytest.mark.asyncio
    async def test_reads_an_explicit_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _responder(monkeypatch, lambda r: httpx.Response(429, headers={"retry-after": "120"}))
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.retry_after_seconds == 120

    @pytest.mark.asyncio
    async def test_ignores_an_unparseable_retry_after(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The HTTP-date form is legal; misreading it as seconds would be nonsense."""
        _responder(
            monkeypatch,
            lambda r: httpx.Response(429, headers={"retry-after": "Wed, 01 Sep 2026 12:00:00 GMT"}),
        )
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.retry_after_seconds is None and outcome.retryable

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403, 413])
    async def test_our_own_mistakes_are_terminal_but_do_not_prune(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rejected token says nothing about whether the browser still exists."""
        _responder(monkeypatch, lambda r: httpx.Response(status))
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert not outcome.retryable and not outcome.expired and not outcome.accepted

    @pytest.mark.asyncio
    async def test_a_timeout_is_retryable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        _responder(monkeypatch, timeout)
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.retryable and outcome.error_code == "WEB_PUSH_TIMEOUT"

    @pytest.mark.asyncio
    async def test_sends_the_headers_a_push_service_requires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        await send_web_push(
            endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}", urgency="high"
        )
        headers = seen[0].headers
        assert headers["content-encoding"] == "aes128gcm"
        assert headers["content-type"] == "application/octet-stream"
        assert headers["authorization"].startswith("vapid t=")
        assert f"k={VAPID_PUBLIC}" in headers["authorization"]
        assert headers["urgency"] == "high"
        assert int(headers["ttl"]) > 0
        # The body must be ciphertext, never the plaintext notification.
        assert seen[0].content != b"{}"

    @pytest.mark.asyncio
    async def test_refuses_an_endpoint_that_is_no_longer_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row stored before the allowlist tightened must not keep its permission."""
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        _patch_settings(
            monkeypatch, _settings(WEB_PUSH_ALLOWED_ENDPOINT_HOSTS=["only.example.test"])
        )
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.error_code == "WEB_PUSH_ENDPOINT_REJECTED"
        assert not outcome.retryable
        assert seen == [], "nothing may be sent to a disallowed host"

    @pytest.mark.asyncio
    async def test_a_misconfigured_subject_fails_loudly_rather_than_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some services drop pushes signed without `sub`, returning a cheerful 201."""
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        monkeypatch.setattr(web_push_delivery, "settings", _settings(WEB_PUSH_VAPID_SUBJECT=""))
        outcome = await send_web_push(endpoint=CHROME, p256dh=P256DH, auth=AUTH, payload=b"{}")
        assert outcome.error_code == "WEB_PUSH_MISCONFIGURED"
        assert seen == []


# ---- dispatcher ----------------------------------------------------------------------------


def _policy(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "engagement_enabled": True,
        "timezone": "UTC",
        "timezone_source": "MANUAL",
        "quiet_hours_start": None,
        "quiet_hours_end": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _notification(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "n1",
        "user_id": "u1",
        "type": "learning.study_session_reminder",
        "category": "LEARNING",
        "title": "Time to study",
        "body": "Your session starts soon.",
        "action": {"kind": "OPEN_SESSION", "v": 1},
        "priority": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _installation(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "i1",
        "endpoint": CHROME,
        "p256dh_encrypted": encrypt_subscription_secret(P256DH),
        "auth_encrypted": encrypt_subscription_secret(AUTH),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@dataclass
class FakeRepo:
    """Records what the dispatcher decided, so assertions read as ledger outcomes."""

    policy: Any = None
    legacy: Any = None
    override: Any = None
    claimed: list[tuple[Any, Any, Any]] = field(default_factory=list)
    suppressed: list[tuple[str, str]] = field(default_factory=list)
    deferred: list[tuple[str, datetime]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.policy = self.policy or _policy()
        self.legacy = (
            self.legacy if self.legacy is not None else SimpleNamespace(notifications=True)
        )
        self.override = self.override or SimpleNamespace(enabled=True, frequency="IMMEDIATE")

    async def channel_policy(self, user_id, notification_type, category, channel):
        assert channel == "WEB_PUSH"
        return {"policy": self.policy, "legacy": self.legacy, "override": self.override}

    async def claim_due_web_push_deliveries(self, *, limit, now):
        return self.claimed

    async def suppress_delivery(self, delivery_id, reason):
        self.suppressed.append((delivery_id, reason))

    async def defer_delivery(self, delivery_id, *, next_attempt_at):
        self.deferred.append((delivery_id, next_attempt_at))

    async def record_web_push_result(self, delivery_id, **values):
        self.results.append({"delivery_id": delivery_id, **values})


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(web_push_dispatcher, "notification_repo", fake)
    return fake


def _dispatch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(web_push_dispatcher, "get_settings", lambda: settings)


class TestSendTimeAuthorisation:
    """Planning happened earlier; consent may have changed since."""

    @pytest.mark.asyncio
    async def test_allows_a_consented_immediate_push(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert allowed and reason is None and deferred is None

    @pytest.mark.asyncio
    async def test_the_kill_switch_suppresses_rather_than_defers(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings(WEB_PUSH_ENABLED=False))
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert not allowed and reason == "WEB_PUSH_CHANNEL_DISABLED" and deferred is None

    @pytest.mark.asyncio
    async def test_missing_configuration_defers_rather_than_burning_the_row(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configuration gap is not the learner's fault and is not permanent."""
        broken = _settings(WEB_PUSH_VAPID_PRIVATE_KEY="")
        _dispatch_settings(monkeypatch, broken)
        monkeypatch.setattr(web_push_delivery, "settings", broken)
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert not allowed and reason is None and deferred == NOW + timedelta(minutes=15)

    @pytest.mark.asyncio
    async def test_a_learner_outside_the_cohort_is_deferred(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cohort may include them later; discarding would need the producer to run again."""
        _dispatch_settings(monkeypatch, _settings(WEB_PUSH_ROLLOUT_PERCENT=0))
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert not allowed and reason is None and deferred == NOW + timedelta(minutes=15)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("label", "attribute", "value", "expected"),
        [
            ("engagement off", "policy", _policy(engagement_enabled=False), "ENGAGEMENT_DISABLED"),
            (
                "legacy master off",
                "legacy",
                SimpleNamespace(notifications=False),
                "LEGACY_MASTER_DISABLED",
            ),
            ("no consent row", "override", None, "WEB_PUSH_CONSENT_MISSING"),
            (
                "channel switched off",
                "override",
                SimpleNamespace(enabled=False, frequency="IMMEDIATE"),
                "CHANNEL_DISABLED",
            ),
        ],
    )
    async def test_withdrawn_consent_suppresses_with_a_named_reason(
        self,
        repo: FakeRepo,
        monkeypatch: pytest.MonkeyPatch,
        label: str,
        attribute: str,
        value: Any,
        expected: str,
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        if attribute == "override" and value is None:
            repo.override = None
        else:
            setattr(repo, attribute, value)
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert not allowed and reason == expected and deferred is None

    @pytest.mark.asyncio
    async def test_a_digest_preference_means_no_push_for_this_item(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A digest is an email arrangement; a push days late is worse than none."""
        _dispatch_settings(monkeypatch, _settings())
        repo.override = SimpleNamespace(enabled=True, frequency="DIGEST")
        allowed, reason, _ = await web_push_dispatcher._web_push_allowed(_notification(), NOW)
        assert not allowed and reason == "HELD_FOR_DIGEST"

    @pytest.mark.asyncio
    async def test_quiet_hours_defer_to_the_end_of_the_window(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        repo.policy = _policy(quiet_hours_start="08:00", quiet_hours_end="20:00")
        allowed, reason, deferred = await web_push_dispatcher._web_push_allowed(
            _notification(), NOW
        )
        assert not allowed and reason == "QUIET_HOURS"
        assert deferred is not None and deferred > NOW


class TestDispatchEvidence:
    @pytest.mark.asyncio
    async def test_records_an_accepted_push(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        _responder(
            monkeypatch, lambda r: httpx.Response(201, headers={"location": "https://push/m/9"})
        )
        delivery = SimpleNamespace(id="d1", attempt_count=1)
        repo.claimed = [(delivery, _notification(), _installation())]

        assert await web_push_dispatcher.dispatch_due_web_push() == 1
        assert len(repo.results) == 1
        result = repo.results[0]
        assert result["accepted"] is True
        assert result["expired"] is False
        assert result["provider_message_id"] == "https://push/m/9"
        assert repo.suppressed == [] and repo.deferred == []

    @pytest.mark.asyncio
    async def test_a_gone_subscription_is_reported_as_expired(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        _responder(monkeypatch, lambda r: httpx.Response(410))
        repo.claimed = [
            (SimpleNamespace(id="d1", attempt_count=1), _notification(), _installation())
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        result = repo.results[0]
        assert result["expired"] is True and result["accepted"] is False
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_unreadable_key_material_prunes_without_calling_the_push_service(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As final as a 410: nothing can be encrypted to this subscription again."""
        _dispatch_settings(monkeypatch, _settings())
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        repo.claimed = [
            (
                SimpleNamespace(id="d1", attempt_count=1),
                _notification(),
                _installation(p256dh_encrypted="v1.corrupted"),
            )
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        result = repo.results[0]
        assert result["expired"] is True
        assert result["error_code"] == "WEB_PUSH_KEYS_UNREADABLE"
        assert seen == [], "a subscription we cannot encrypt to must not be contacted"

    @pytest.mark.asyncio
    async def test_honours_a_retry_after_longer_than_our_backoff(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        _responder(monkeypatch, lambda r: httpx.Response(429, headers={"retry-after": "3000"}))
        repo.claimed = [
            (SimpleNamespace(id="d1", attempt_count=1), _notification(), _installation())
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        result = repo.results[0]
        assert result["retryable"] is True
        assert result["next_attempt_at"] > datetime.now(UTC) + timedelta(seconds=2400)

    @pytest.mark.asyncio
    async def test_a_time_critical_item_is_sent_with_high_urgency(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        repo.claimed = [
            (
                SimpleNamespace(id="d1", attempt_count=1),
                _notification(priority=1),
                _installation(),
            )
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        assert seen[0].headers["urgency"] == "high"

    @pytest.mark.asyncio
    async def test_a_routine_item_does_not_wake_the_device(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        repo.claimed = [
            (SimpleNamespace(id="d1", attempt_count=1), _notification(priority=5), _installation())
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        assert seen[0].headers["urgency"] == "normal"

    @pytest.mark.asyncio
    async def test_withdrawn_consent_stops_the_send_after_claiming(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        seen = _responder(monkeypatch, lambda r: httpx.Response(201))
        repo.override = SimpleNamespace(enabled=False, frequency="IMMEDIATE")
        repo.claimed = [
            (SimpleNamespace(id="d1", attempt_count=1), _notification(), _installation())
        ]

        await web_push_dispatcher.dispatch_due_web_push()
        assert repo.suppressed == [("d1", "CHANNEL_DISABLED")]
        assert repo.results == []
        assert seen == [], "no push may leave after consent was withdrawn"

    @pytest.mark.asyncio
    async def test_an_empty_queue_does_no_work(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _dispatch_settings(monkeypatch, _settings())
        assert await web_push_dispatcher.dispatch_due_web_push() == 0


class TestRetryBackoff:
    def test_grows_and_is_jittered_per_delivery(self) -> None:
        first = web_push_dispatcher._retry_at("d1", 1, NOW)
        later = web_push_dispatcher._retry_at("d1", 4, NOW)
        assert NOW < first < later
        other = web_push_dispatcher._retry_at("d2", 1, NOW)
        assert other != first, "identical retry times would arrive in lockstep"

    def test_is_capped(self) -> None:
        assert web_push_dispatcher._retry_at("d1", 20, NOW) < NOW + timedelta(seconds=4600)
