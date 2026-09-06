"""Billing webhooks refuse what they cannot verify.

These three endpoints are unauthenticated by construction — a payment provider cannot present a
bearer token for one of our users — so the signature *is* the authentication. All three were
written to fail open, and that was invisible for as long as the router was commented out in
`app.py`. Mounting it in Phase 1 turned three trusting endpoints live at once **without a line of
`webhooks.py` appearing in the diff**, which is why this file exists: the defect was in the
interaction between a mount and a default, and no test of either half would have caught it.

The Stripe handler writes `User.tier`. With an unset secret it parsed an unverified body, so any
caller could have posted a crafted `customer.subscription.updated` and granted themselves
`PREMIUM_MONTHLY`. That is the whole reason this is a `503` rather than a warning log.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_billing_webhook_auth.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.billing import webhooks  # noqa: E402


def _settings(**overrides) -> Settings:
    """A Settings instance with the webhook secrets blanked unless a test sets one."""
    base = {
        "STRIPE_WEBHOOK_SECRET": "",
        "PAYSTACK_SECRET_KEY": "",
        "GOOGLE_PUBSUB_AUDIENCE": "",
        "GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL": "",
    }
    base.update(overrides)
    return Settings(**base)


class _Request:
    """The two methods the handlers call. Not a mock of Starlette, just the surface used."""

    def __init__(self, payload: bytes = b"{}"):
        self._payload = payload

    async def body(self) -> bytes:
        return self._payload

    async def json(self):
        return json.loads(self._payload)


# ---------------------------------------------------------------------------
# A missing secret is a refusal, not a bypass
# ---------------------------------------------------------------------------


async def test_stripe_refuses_when_the_signing_secret_is_unset():
    """The case that let a caller promote themselves.

    Previously: `logger.warning(...)` then `json.loads(body)`, and on to a handler that writes
    `User.tier` and the subscription period.
    """
    with pytest.raises(HTTPException) as exc:
        await webhooks.stripe_webhook(
            request=_Request(), stripe_signature="t=1,v1=whatever", settings=_settings()
        )
    assert exc.value.status_code == 503


async def test_paystack_refuses_when_the_secret_key_is_unset():
    """The bug here was the shape of the condition, not a missing check.

    `if settings.PAYSTACK_SECRET_KEY and not _verify(...)` short-circuits to `False` when the key
    is empty, so an unconfigured deployment skipped verification instead of failing it. This is
    the launch market's rail.
    """
    with pytest.raises(HTTPException) as exc:
        await webhooks.paystack_webhook(
            request=_Request(), x_paystack_signature="sig", settings=_settings()
        )
    assert exc.value.status_code == 503


async def test_google_play_rtdn_refuses_when_unconfigured():
    """This endpoint had no authentication of any kind, under any configuration."""
    with pytest.raises(HTTPException) as exc:
        await webhooks.google_play_rtdn(
            request=_Request(), authorization=None, settings=_settings()
        )
    assert exc.value.status_code == 503


def test_the_refusal_is_503_so_a_real_event_survives_the_gap():
    """`503`, not `500` or `403`.

    Providers retry on `503`, so a genuine event that arrives between mounting the endpoint and
    setting the secret is redelivered rather than lost. A `403` would tell Stripe the event was
    rejected on its merits and some providers stop retrying.
    """
    assert webhooks._unconfigured("Stripe").status_code == 503


# ---------------------------------------------------------------------------
# A bad signature is a refusal too
# ---------------------------------------------------------------------------


async def test_paystack_rejects_a_bad_signature_when_configured():
    with pytest.raises(HTTPException) as exc:
        await webhooks.paystack_webhook(
            request=_Request(b'{"event":"charge.success"}'),
            x_paystack_signature="deadbeef",
            settings=_settings(PAYSTACK_SECRET_KEY="sk_test_x"),
        )
    assert exc.value.status_code == 400


async def test_rtdn_rejects_a_request_with_no_bearer_token():
    with pytest.raises(HTTPException) as exc:
        await webhooks.google_play_rtdn(
            request=_Request(),
            authorization=None,
            settings=_settings(GOOGLE_PUBSUB_AUDIENCE="https://api.maigie.com/webhooks"),
        )
    assert exc.value.status_code == 401


async def test_rtdn_rejects_a_malformed_authorization_header():
    with pytest.raises(HTTPException) as exc:
        await webhooks.google_play_rtdn(
            request=_Request(),
            authorization="Basic abc123",
            settings=_settings(GOOGLE_PUBSUB_AUDIENCE="https://api.maigie.com/webhooks"),
        )
    assert exc.value.status_code == 401


async def test_rtdn_rejects_a_token_it_cannot_verify():
    """A Google-signed token proves Google minted it, not that our subscription sent it — but an
    unverifiable string does not even get that far."""
    with pytest.raises(HTTPException) as exc:
        await webhooks.google_play_rtdn(
            request=_Request(),
            authorization="Bearer not-a-jwt",
            settings=_settings(GOOGLE_PUBSUB_AUDIENCE="https://api.maigie.com/webhooks"),
        )
    assert exc.value.status_code == 401


async def test_rtdn_rejects_a_valid_token_from_the_wrong_service_account(monkeypatch):
    """Any Google customer can mint a token for our audience; only ours has our email.

    Without this check, `verify_oauth2_token` passing would have been treated as proof the push
    came from our subscription.
    """
    from google.oauth2 import id_token

    monkeypatch.setattr(
        id_token, "verify_oauth2_token", lambda *a, **k: {"email": "someone-else@example.com"}
    )
    with pytest.raises(HTTPException) as exc:
        await webhooks.google_play_rtdn(
            request=_Request(),
            authorization="Bearer looks-fine",
            settings=_settings(
                GOOGLE_PUBSUB_AUDIENCE="https://api.maigie.com/webhooks",
                GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL="rtdn@maigie.iam.gserviceaccount.com",
            ),
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Only a processed event answers 200
# ---------------------------------------------------------------------------


async def test_a_failed_handler_does_not_answer_200(monkeypatch):
    """A swallowed billing event is a payment with no record.

    Every handler used to `except Exception: logger.error(...)` and return `200`, which is
    indistinguishable to the provider from having done the work. It matters most for the provider
    that cannot work yet: `handle_paystack_webhook` reaches a Prisma sentinel until Phase 2b, so
    under the old behaviour a real NGN charge produced no record, no tier change and no alert.
    """
    monkeypatch.setattr(
        webhooks, "_verify_paystack_signature", lambda payload, signature, secret: True
    )

    async def _boom(event, _):
        raise RuntimeError("PrismaClientRemoved")

    import src.domains.billing.services.paystack_service as ps

    monkeypatch.setattr(ps, "handle_paystack_webhook", _boom)

    with pytest.raises(HTTPException) as exc:
        await webhooks.paystack_webhook(
            request=_Request(b'{"event":"charge.success"}'),
            x_paystack_signature="sig",
            settings=_settings(PAYSTACK_SECRET_KEY="sk_test_x"),
        )
    assert exc.value.status_code == 500
