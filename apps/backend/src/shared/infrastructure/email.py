"""Outbound transactional email.

Sends multipart (HTML + plaintext) mail through SMTP and/or the Resend HTTP API,
ordered by ``settings.EMAIL_OUTBOUND_STRATEGY`` so a provider quota failure falls
through to the next provider instead of dropping the message.

Restored from the pre-migration ``services/email`` module, which this package had
replaced with silent no-op stubs. Deliberate changes made during the restore:

* ``fastapi_mail`` is no longer imported. It only ever supplied a
  ``ConnectionConfig`` object that was read for three boolean flags; the actual
  sending has always been stdlib ``smtplib``. The flags are now module constants.
* Sender address and frontend base URL are resolved per-send rather than snapshotted
  at import time, so settings changed after import (notably in tests) take effect.
* ``send_space_invite_email`` takes ``(to_email, inviter_name, space_name)`` to match
  its only caller, which passes those three positionally. The stub it replaces
  declared ``(to_email, space_name, inviter_name, ...)``, so an implementation
  written against the stub would have addressed the space by the inviter's name.
"""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings

logger = logging.getLogger(__name__)

TEMPLATE_FOLDER = Path(__file__).resolve().parents[2] / "templates" / "email"

# Autoescape HTML templates so names and space titles cannot inject markup, but
# leave .txt alone or plaintext parts would gain HTML entities.
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_FOLDER)),
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=False),
)

RESEND_API_URL = "https://api.resend.com/emails"

APP_NAME = "Maigie"
DEFAULT_FROM_EMAIL = "noreply@maigie.com"

# Previously read off fastapi_mail's ConnectionConfig.
SMTP_STARTTLS = True
SMTP_SSL_TLS = False
SMTP_USE_CREDENTIALS = True

_VALID_OUTBOUND_STRATEGIES: dict[str, tuple[str, ...]] = {
    "smtp_then_resend": ("smtp", "resend"),
    "resend_then_smtp": ("resend", "smtp"),
    "resend_only": ("resend",),
    "smtp_only": ("smtp",),
}

# Substrings in an SMTP failure that suggest a provider quota or plan limit rather
# than a bad address, meaning the next provider in the chain is worth trying.
_QUOTA_HINTS = (
    "quota",
    "credit",
    "limit",
    "exceeded",
    "552",
    "451",
    "450",
    "daily",
    "monthly",
    "plan",
    "not enough",
    "suspended",
)


def _from_email() -> str:
    return settings.EMAILS_FROM_EMAIL or DEFAULT_FROM_EMAIL


def _from_name() -> str:
    return settings.EMAILS_FROM_NAME or APP_NAME


def _email_transport_configured() -> bool:
    """True if we can send via SMTP and/or the Resend fallback."""
    return bool(settings.SMTP_HOST) or bool(settings.RESEND_API_KEY)


def _outbound_provider_order() -> tuple[str, ...]:
    """Ordered providers for this send, from ``EMAIL_OUTBOUND_STRATEGY``."""
    raw = (settings.EMAIL_OUTBOUND_STRATEGY or "smtp_then_resend").strip().lower()
    return _VALID_OUTBOUND_STRATEGIES.get(raw, ("smtp", "resend"))


def _smtp_error_suggests_quota(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc!s}".lower()
    return any(needle in text for needle in _QUOTA_HINTS)


def _get_frontend_base_url() -> str:
    """Base URL for links embedded in emails."""
    return (settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200").rstrip(
        "/"
    )


def _standard_headers(ref_id: str) -> dict[str, str]:
    return {
        "Reply-To": _from_email(),
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": ref_id,
    }


def _render(template_base: str, fallback_text: str, **data: object) -> tuple[str, str]:
    """Render ``<base>.html`` and ``<base>.txt``, falling back if there is no text part.

    The HTML template is required; a missing plaintext template is normal for some
    messages and is replaced by ``fallback_text`` so the multipart message always
    has both parts.
    """
    html_body = jinja_env.get_template(f"{template_base}.html").render(**data)
    try:
        text_body = jinja_env.get_template(f"{template_base}.txt").render(**data)
    except Exception:
        text_body = fallback_text
    return html_body, text_body


def _send_multipart_email_sync(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> None:
    """Blocking SMTP send of a multipart HTML + plaintext message."""
    multipart_msg = MIMEMultipart("alternative")
    multipart_msg["Subject"] = subject
    multipart_msg["To"] = to_email
    multipart_msg["From"] = f"{_from_name()} <{_from_email()}>"

    if headers:
        for key, value in headers.items():
            multipart_msg[key] = value

    # Plaintext first: the last part of an alternative message wins in most clients.
    multipart_msg.attach(MIMEText(text_body, "plain", "utf-8"))
    multipart_msg.attach(MIMEText(html_body, "html", "utf-8"))

    smtp_host = settings.SMTP_HOST or "localhost"
    smtp_port = settings.SMTP_PORT or 587

    server: smtplib.SMTP | smtplib.SMTP_SSL | None = None
    try:
        if SMTP_SSL_TLS:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)
            if SMTP_STARTTLS:
                server.starttls()

        if SMTP_USE_CREDENTIALS and settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")

        # send_message reports partially refused recipients instead of raising.
        refused = server.send_message(multipart_msg)
        if refused:
            raise smtplib.SMTPException(f"SMTP server refused recipient(s): {refused}")
    except Exception as exc:
        if _smtp_error_suggests_quota(exc):
            logger.warning(
                "SMTP error for %s looks like a provider quota or plan limit; "
                "the next outbound provider will be tried if one is configured: %s",
                to_email,
                exc,
            )
        else:
            logger.error("SMTP error sending email to %s: %s", to_email, exc)
        raise
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    logger.debug("Failed to close SMTP connection", exc_info=True)


async def _send_via_resend(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> None:
    """Send through the Resend HTTP API."""
    from_addr = settings.RESEND_FROM_EMAIL or _from_email()
    payload: dict[str, object] = {
        "from": f"{_from_name()} <{from_addr}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }

    extra_headers: dict[str, str] = {}
    if headers:
        for key, value in headers.items():
            if key.lower() == "reply-to":
                payload["reply_to"] = value
            else:
                extra_headers[key] = value
    if extra_headers:
        payload["headers"] = extra_headers

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        try:
            detail: object = response.json()
        except Exception:
            detail = response.text
        logger.error(
            "Resend API error sending to %s: HTTP %s %s",
            to_email,
            response.status_code,
            detail,
        )
        raise RuntimeError(f"Resend send failed with HTTP {response.status_code}")


async def _send_multipart_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> None:
    """Send via each configured provider in strategy order until one succeeds.

    Quota failures at Brevo and similar providers surface as SMTP exceptions or
    refused recipients, which fall through to the next provider. A provider that
    accepts the message and then drops it silently cannot be detected here; switch
    to ``resend_then_smtp`` or ``resend_only`` until the quota resets.
    """
    chain = _outbound_provider_order()
    last_error: BaseException | None = None
    tried: list[str] = []

    for provider in chain:
        if provider == "smtp":
            if not settings.SMTP_HOST:
                continue
            try:
                await asyncio.to_thread(
                    _send_multipart_email_sync,
                    to_email,
                    subject,
                    html_body,
                    text_body,
                    headers,
                )
            except Exception as exc:
                last_error = exc
                tried.append("smtp")
                logger.warning("Outbound smtp failed to=%s subject=%r: %s", to_email, subject, exc)
            else:
                logger.info(
                    "Outbound email delivered via=smtp to=%s subject=%r tried=%s",
                    to_email,
                    subject,
                    tried,
                )
                return

        elif provider == "resend":
            if not settings.RESEND_API_KEY:
                continue
            try:
                await _send_via_resend(
                    to_email=to_email,
                    subject=subject,
                    html_body=html_body,
                    text_body=text_body,
                    headers=headers,
                )
            except Exception as exc:
                last_error = exc
                tried.append("resend")
                logger.warning(
                    "Outbound resend failed to=%s subject=%r: %s", to_email, subject, exc
                )
            else:
                logger.info(
                    "Outbound email delivered via=resend to=%s subject=%r tried=%s",
                    to_email,
                    subject,
                    tried,
                )
                return

    if last_error is not None:
        raise last_error
    raise RuntimeError(
        "No usable outbound email provider for this strategy "
        f"(chain={chain!s}, SMTP_HOST={'set' if settings.SMTP_HOST else 'unset'}, "
        f"RESEND_API_KEY={'set' if settings.RESEND_API_KEY else 'unset'})"
    )


async def send_space_invite_email(
    to_email: str,
    inviter_name: str,
    space_name: str,
    invite_url: str | None = None,
    **_kwargs: object,
) -> None:
    """Invite someone to a learning space.

    Argument order matches the caller in ``learning_spaces.services.space_impl``,
    which passes ``(email, inviter_name, space_name)`` positionally.
    """
    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping space invite email to %s",
            to_email,
        )
        return

    spaces_url = invite_url or f"{_get_frontend_base_url()}/spaces"
    template_data = {
        "inviter_name": inviter_name,
        "space_name": space_name,
        "spaces_url": spaces_url,
        "app_name": APP_NAME,
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_body, text_body = _render(
        "space_invite",
        f'{inviter_name} invited you to join the learning space "{space_name}" '
        f"on {APP_NAME}. Open it here: {spaces_url}",
        **template_data,
    )

    try:
        await _send_multipart_email(
            to_email=to_email,
            subject=f"{inviter_name} invited you to a learning space on {APP_NAME}",
            html_body=html_body,
            text_body=text_body,
            headers=_standard_headers(f"space-invite-{to_email}"),
        )
    except Exception:
        logger.exception("Failed to send space invite email to %s", to_email)


async def send_limit_reached_email(
    email: str | None = None,
    name: str | None = None,
    user_id: str | None = None,
    **_kwargs: object,
) -> None:
    """Tell a user they have reached their monthly limit."""
    if not email:
        logger.warning(
            "send_limit_reached_email called without an address (user_id=%s); skipping",
            user_id,
        )
        return

    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping limit reached email to %s",
            email,
        )
        return

    subscription_url = f"{_get_frontend_base_url()}/settings?tab=subscription"
    template_data = {
        "name": name or "there",
        "subscription_url": subscription_url,
        "app_name": APP_NAME,
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_body, text_body = _render(
        "limit_reached",
        f"You've reached your monthly limit. Start a free trial: {subscription_url}",
        **template_data,
    )

    try:
        await _send_multipart_email(
            to_email=email,
            subject=f"You've reached your monthly {APP_NAME} limit",
            html_body=html_body,
            text_body=text_body,
            headers=_standard_headers(f"limit-reached-{email}"),
        )
    except Exception:
        logger.exception("Failed to send limit reached email to %s", email)


_TIER_DISPLAY_NAMES = {
    # Current plan ids.
    "plus_monthly": "Maigie Plus Monthly",
    "plus_yearly": "Maigie Plus Yearly",
    "maigie_plus_monthly": "Maigie Plus Monthly",
    "maigie_plus_yearly": "Maigie Plus Yearly",
    "circle_plan_monthly": "Circle Plan Monthly",
    "plus_seat_add_on_monthly": "Plus Seat Add-on",
    # Retired ids still reachable through historical webhook replays.
    "PREMIUM_MONTHLY": "Maigie Plus Monthly",
    "PREMIUM_YEARLY": "Maigie Plus Yearly",
    "STUDY_CIRCLE_MONTHLY": "Study Circle Monthly",
    "STUDY_CIRCLE_YEARLY": "Study Circle Yearly",
    "SQUAD_MONTHLY": "Squad Plan Monthly",
    "SQUAD_YEARLY": "Squad Plan Yearly",
}


async def send_subscription_success_email(
    email: str,
    name: str,
    tier: str,
    **_kwargs: object,
) -> None:
    """Confirm a successful subscription.

    Called from the Stripe and Paystack webhook handlers, so it must never raise:
    an email failure must not cause a webhook to be retried as though the payment
    had not been recorded.
    """
    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping subscription email to %s",
            email,
        )
        return

    tier_name = _TIER_DISPLAY_NAMES.get(str(tier)) or str(tier).replace("_", " ").title()
    dashboard_url = f"{_get_frontend_base_url()}/dashboard"
    template_data = {
        "name": name,
        "tier_name": tier_name,
        "dashboard_url": dashboard_url,
        "app_name": APP_NAME,
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_body, text_body = _render(
        "subscription_success",
        f"Your {tier_name} subscription is active. Open {APP_NAME}: {dashboard_url}",
        **template_data,
    )

    try:
        await _send_multipart_email(
            to_email=email,
            subject=f"Your {tier_name} subscription is active",
            html_body=html_body,
            text_body=text_body,
            headers=_standard_headers(f"subscription-success-{email}"),
        )
    except Exception:
        logger.exception("Failed to send subscription success email to %s", email)


async def send_bulk_email(
    email: str,
    name: str | None = None,
    subject: str = "",
    content: str = "",
    **_kwargs: object,
) -> None:
    """Send a one-off transactional message with caller-supplied HTML ``content``."""
    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping bulk email to %s",
            email,
        )
        return

    template_data = {
        "name": name or "there",
        "subject": subject,
        "content": content,
        "app_name": APP_NAME,
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_body, text_body = _render("bulk_email", content, **template_data)

    try:
        await _send_multipart_email(
            to_email=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=_standard_headers(f"bulk-{email}"),
        )
    except Exception:
        logger.exception("Failed to send bulk email to %s", email)


async def send_weekly_summaries() -> None:
    """Not restored yet.

    The original lived in a separate ``weekly_summary_email_service`` module with its
    own repository dependencies and has not been migrated. It raises rather than
    returning quietly so that the ``notifications.weekly_summary`` beat task fails
    visibly instead of reporting success every week while sending nothing.
    """
    raise NotImplementedError(
        "send_weekly_summaries has not been migrated from "
        "services/weekly_summary_email_service; the notifications.weekly_summary "
        "beat task cannot deliver until it is restored"
    )
