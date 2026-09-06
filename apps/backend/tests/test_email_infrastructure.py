"""Tests for the restored outbound email module and the unmigrated-datastore sentinel.

``shared/infrastructure/email.py`` was a set of ``pass`` stubs, so space invites and
limit-reached notices were accepted and discarded. These tests cover the parts of the
restored module that are easy to get wrong and impossible to notice in production:
the provider fallback chain, the HTML/plaintext escaping split, and the argument
order that a caller already depends on.

No test here opens a socket. The SMTP and Resend transports are substituted.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import inspect  # noqa: E402

import pytest  # noqa: E402

from src.config import settings  # noqa: E402
from src.shared.infrastructure import email as em  # noqa: E402
from src.shared.infrastructure.unmigrated import (  # noqa: E402
    PrismaClientRemoved,
    UnmigratedDatastoreError,
)


@pytest.fixture
def transport(monkeypatch):
    """Configure both providers *usably* and capture what each one is asked to send.

    The credentials matter now. This module always authenticates, so a host without a username
    and password is a provider that can only ever be refused, and the transport skips it rather
    than spending a round trip proving it. The fixture used to set the host alone, which quietly
    described a configuration that could never send.
    """
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer@example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test", raising=False)
    monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", "smtp_then_resend", raising=False)

    sent: dict[str, list[dict]] = {"smtp": [], "resend": []}

    def fake_smtp(to_email, subject, html_body, text_body, headers=None):
        sent["smtp"].append({"to": to_email, "subject": subject, "html": html_body})

    async def fake_resend(**kwargs):
        sent["resend"].append({"to": kwargs["to_email"], "subject": kwargs["subject"]})

    monkeypatch.setattr(em, "_send_multipart_email_sync", fake_smtp)
    monkeypatch.setattr(em, "_send_via_resend", fake_resend)
    return sent


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy,expected",
    [
        ("smtp_then_resend", ("smtp", "resend")),
        ("resend_then_smtp", ("resend", "smtp")),
        ("resend_only", ("resend",)),
        ("smtp_only", ("smtp",)),
        ("  SMTP_THEN_RESEND  ", ("smtp", "resend")),
        ("not-a-strategy", ("smtp", "resend")),
        ("", ("smtp", "resend")),
    ],
)
def test_outbound_provider_order(monkeypatch, strategy, expected):
    monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", strategy, raising=False)
    assert em._outbound_provider_order() == expected


@pytest.mark.parametrize(
    "message",
    ["552 daily quota exceeded", "451 try again later", "plan limit reached", "account suspended"],
)
def test_quota_like_smtp_errors_are_recognised(message):
    assert em._smtp_error_suggests_quota(Exception(message))


@pytest.mark.parametrize("message", ["no such mailbox", "invalid recipient", "bad syntax"])
def test_ordinary_smtp_errors_are_not_treated_as_quota(message):
    assert not em._smtp_error_suggests_quota(Exception(message))


async def test_smtp_quota_failure_falls_through_to_resend(transport, monkeypatch):
    def quota_failure(*args, **kwargs):
        raise OSError("552 monthly quota exceeded")

    monkeypatch.setattr(em, "_send_multipart_email_sync", quota_failure)

    await em.send_space_invite_email("invitee@example.com", "Ada", "Organic Chemistry")

    assert transport["resend"] == [
        {
            "to": "invitee@example.com",
            "subject": "Ada invited you to a learning space on Maigie",
        }
    ]


async def test_successful_smtp_does_not_also_send_via_resend(transport):
    await em.send_space_invite_email("invitee@example.com", "Ada", "Organic Chemistry")

    assert len(transport["smtp"]) == 1
    assert transport["resend"] == []


async def test_every_provider_failing_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test", raising=False)

    def smtp_failure(*args, **kwargs):
        raise OSError("smtp down")

    async def resend_failure(**kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(em, "_send_multipart_email_sync", smtp_failure)
    monkeypatch.setattr(em, "_send_via_resend", resend_failure)

    with pytest.raises(RuntimeError):
        await em._send_multipart_email("a@b.c", "subject", "<p>html</p>", "text")


async def test_no_configured_provider_raises_a_message_naming_both_settings(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", None, raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)

    with pytest.raises(RuntimeError, match="No usable outbound email provider"):
        await em._send_multipart_email("a@b.c", "subject", "<p>html</p>", "text")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_space_invite_template_uses_space_vocabulary():
    html, text = em._render(
        "space_invite",
        "fallback",
        inviter_name="Ada",
        space_name="Organic Chemistry",
        spaces_url="https://app.example/spaces",
        app_name="Maigie",
        logo_url="",
    )
    for body in (html, text):
        assert "Ada" in body
        assert "Organic Chemistry" in body
        assert "https://app.example/spaces" in body
        # The templates were renamed from circle_invite during the space rename.
        assert "circle" not in body.lower()


def test_html_is_escaped_but_plaintext_is_not():
    html, text = em._render(
        "space_invite",
        "fallback",
        inviter_name="<script>alert(1)</script>",
        space_name="Physics & Maths",
        spaces_url="https://app.example/spaces?a=1&b=2",
        app_name="Maigie",
        logo_url="",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # A plaintext part must not carry HTML entities.
    assert "&amp;" not in text
    assert "Physics & Maths" in text


def test_missing_plaintext_template_falls_back_without_raising():
    html, text = em._render(
        "space_invite",
        "the fallback text",
        inviter_name="Ada",
        space_name="S",
        spaces_url="u",
        app_name="Maigie",
        logo_url="",
    )
    assert html
    assert text != "the fallback text"  # space_invite.txt exists


def test_html_only_template_uses_the_fallback_text(monkeypatch):
    """A template with no .txt sibling still yields a plaintext part.

    Every shipped template currently has one, so the loader is swapped for an
    in-memory one rather than pinning the test to whichever template happens to
    be missing its text version.
    """
    from jinja2 import DictLoader, Environment, select_autoescape

    html_only = Environment(
        loader=DictLoader({"html_only.html": "<p>{{ name }}</p>"}),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
    )
    monkeypatch.setattr(em, "jinja_env", html_only)

    html, text = em._render("html_only", "the fallback text", name="Ada")
    assert html == "<p>Ada</p>"
    assert text == "the fallback text"


# ---------------------------------------------------------------------------
# Caller contracts
# ---------------------------------------------------------------------------


def test_space_invite_argument_order_matches_its_caller():
    """``space_impl`` passes (email, inviter_name, space_name) positionally.

    The stub this replaced declared (to_email, space_name, inviter_name), which would
    have addressed the space by the inviter's name.
    """
    params = list(inspect.signature(em.send_space_invite_email).parameters)
    assert params[:3] == ["to_email", "inviter_name", "space_name"]


async def test_limit_reached_without_an_address_is_a_no_op(transport):
    await em.send_limit_reached_email(email=None, name="Ada", user_id="u1")
    assert transport["smtp"] == []
    assert transport["resend"] == []


async def test_unconfigured_transport_skips_instead_of_raising(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", None, raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)
    # Must not raise: a missing mail configuration should not break a signup or a
    # credit-limit check.
    await em.send_limit_reached_email(email="a@b.c", name="Ada")
    await em.send_space_invite_email("a@b.c", "Ada", "Space")
    await em.send_subscription_success_email("a@b.c", "Ada", "plus_monthly")


async def test_send_failure_does_not_propagate_to_a_webhook_caller(transport, monkeypatch):
    """A mail outage must not make Stripe or Paystack retry a recorded payment."""

    def smtp_failure(*args, **kwargs):
        raise OSError("smtp down")

    async def resend_failure(**kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(em, "_send_multipart_email_sync", smtp_failure)
    monkeypatch.setattr(em, "_send_via_resend", resend_failure)

    await em.send_subscription_success_email("a@b.c", "Ada", "plus_monthly")


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("plus_monthly", "Maigie Plus Monthly"),
        ("plus_yearly", "Maigie Plus Yearly"),
        ("circle_plan_monthly", "Circle Plan Monthly"),
        ("PREMIUM_MONTHLY", "Maigie Plus Monthly"),
        ("something_unknown", "Something Unknown"),
    ],
)
async def test_subscription_email_names_the_tier(transport, tier, expected):
    await em.send_subscription_success_email("a@b.c", "Ada", tier)
    assert transport["smtp"][0]["subject"] == f"Your {expected} subscription is active"


def test_the_email_module_no_longer_re_exports_notification_producers():
    """Producers do not belong here, and the re-export that put them here is gone.

    ``send_weekly_summaries`` used to be re-exported from this module purely because the beat
    task imported it from here, while the progress domain owned the data — an indirection that
    made the email module look like the owner of a notification producer. The task now calls
    the progress service directly, and the summary reaches an inbox through the notification
    orchestrator rather than through a direct send, so neither shim has a reason to exist.

    ``send_schedule_reminder_email`` is gone for the same reason: the reminder is a canonical
    notification now, and a second way to send one would be a second place for consent to be
    forgotten.
    """
    assert not hasattr(em, "send_weekly_summaries")
    assert not hasattr(em, "send_schedule_reminder_email")


def test_the_shared_transport_still_serves_its_remaining_direct_callers():
    """Billing receipts and the intelligence email skill are not engagement notifications.

    They are deterministic, user-commanded messages, so they keep sending directly. Removing
    ``send_bulk_email`` alongside the producers would have broken both.
    """
    assert callable(em.send_bulk_email)
    assert callable(em.send_transactional_email)
    assert callable(em.send_templated_email)


# ---------------------------------------------------------------------------
# Unmigrated datastore sentinel
# ---------------------------------------------------------------------------


def test_prisma_sentinel_raises_on_attribute_access_naming_the_owner():
    db = PrismaClientRemoved("billing.services.paystack_service")
    with pytest.raises(UnmigratedDatastoreError) as excinfo:
        db.user.find_unique(where={"id": "1"})

    message = str(excinfo.value)
    assert "billing.services.paystack_service" in message
    assert "no data was read or written" in message


def test_prisma_sentinel_is_truthy_so_none_guards_do_not_mask_it():
    """Prisma-era code does ``if db_client is None: db_client = db``.

    A falsy sentinel would let some guards skip the assignment and fail later
    somewhere less obvious.
    """
    assert bool(PrismaClientRemoved("x")) is True
