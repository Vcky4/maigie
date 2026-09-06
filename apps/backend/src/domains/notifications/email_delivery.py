"""Turning one canonical notification into one email, and reporting what happened.

The notification domain owns *what* an email says and *whether* it may be sent; the shared
transport owns provider I/O. This module is the seam, and it exists so the dispatcher can
write evidence: `EmailOutcome` distinguishes accepted from refused, and refused-for-now from
refused-forever, which a bare exception could not.

**Why the link goes to the notification centre.** The backend does not emit client routes
(the clients own route mapping, and an email cannot run their resolver), so rather than
inventing a route per action kind in a second place, every email links to the notification
centre with the item's id. That URL resolves today. Deep-linking straight to the action is a
client change: the web app has to read the `open` parameter, which it does not do yet.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.config import settings
from src.shared.infrastructure.email import (
    APP_NAME,
    EmailProviderError,
    _render,
    _send_multipart_email,
    _standard_headers,
)

from .unsubscribe import create_unsubscribe_token

logger = logging.getLogger(__name__)

#: Why this message arrived, in the learner's terms, per canonical category.
_CATEGORY_REASON: dict[str, str] = {
    "LEARNING": "you asked Maigie to email you about your learning",
    "PROGRESS": "you asked Maigie to email you about your progress",
    "SOCIAL": "you asked Maigie to email you about classroom and social activity",
    "CLASSROOM": "you asked Maigie to email you about classroom and social activity",
    "OPERATIONS": "you asked Maigie to email you product updates",
}


@dataclass(frozen=True)
class EmailOutcome:
    """One provider attempt, described in the terms the delivery ledger records."""

    accepted: bool
    provider: str | None
    provider_message_id: str | None
    retryable: bool
    error_code: str | None
    error_detail: str | None
    duration_ms: int


def address_reference(email: str) -> str:
    """A stable, non-reversible handle for an address, for correlation without storing it."""

    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _frontend_base_url() -> str:
    return (settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200").rstrip(
        "/"
    )


def notification_urls(notification_id: str) -> tuple[str, str]:
    """Return ``(action_url, settings_url)`` — both routes the web app already serves."""

    base = _frontend_base_url()
    return f"{base}/notifications?open={notification_id}", f"{base}/settings?tab=notifications"


#: The four settings categories, keyed by the database categories they cover, so an email can
#: offer to switch off the group the learner recognises rather than one notification type.
_UNSUBSCRIBE_SCOPE: dict[str, str] = {
    "LEARNING": "LEARNING",
    "PROGRESS": "PROGRESS",
    "SOCIAL": "SOCIAL_CLASSROOM",
    "CLASSROOM": "SOCIAL_CLASSROOM",
    "OPERATIONS": "PRODUCT_UPDATES",
}


def unsubscribe_urls(user_id: str, category: str | None) -> tuple[str, str]:
    """Return ``(one_click_url, landing_url)`` for this learner and category.

    Two URLs because they serve different callers. The one-click URL is what a mail provider
    POSTs to on the learner's behalf under RFC 8058 and must act without a confirmation step.
    The landing URL is what a person clicking the footer link opens, and it lands on their
    settings so they can see what changed and adjust it rather than only having switched
    something off.
    """

    scope = _UNSUBSCRIBE_SCOPE.get(category or "", "ALL")
    token = create_unsubscribe_token(user_id, scope)  # type: ignore[arg-type]
    base = _frontend_base_url()
    api = f"{settings.API_V1_STR}/notifications/unsubscribe"
    return f"{_api_base_url()}{api}?token={token}", f"{base}/unsubscribe?token={token}"


def _api_base_url() -> str:
    """Where the provider should POST a one-click unsubscribe.

    Falls back to the frontend origin only so a misconfigured environment produces a link that
    is merely wrong rather than one pointing at localhost from a real inbox.
    """

    return (settings.PUBLIC_API_BASE_URL or _frontend_base_url()).rstrip("/")


async def send_notification_email(
    *,
    to_email: str,
    recipient_name: str | None,
    title: str,
    body: str,
    category: str | None,
    notification_id: str,
    user_id: str,
) -> EmailOutcome:
    """Render and send one notification email, never raising for a provider failure.

    A raised exception here would abort a batch and lose the evidence for the message that
    failed, so every provider outcome is returned instead. Only a programming error should
    escape.
    """

    action_url, settings_url = notification_urls(notification_id)
    one_click_url, unsubscribe_url = unsubscribe_urls(user_id, category)
    template_data = {
        "app_name": APP_NAME,
        "logo_url": settings.EMAIL_LOGO_URL or "",
        "title": title,
        "body": body,
        "name": recipient_name or None,
        "action_url": action_url,
        "settings_url": settings_url,
        "category_reason": _CATEGORY_REASON.get(
            category or "", "you have optional Maigie email switched on"
        ),
        "unsubscribe_url": unsubscribe_url,
    }
    html_body, text_body = _render(
        "notification",
        f"{title}\n\n{body}\n\nOpen {APP_NAME}: {action_url}",
        **template_data,
    )

    started = datetime.now(UTC)
    try:
        provider, message_id = await _send_multipart_email(
            to_email=to_email,
            subject=title,
            html_body=html_body,
            text_body=text_body,
            # The notification id, not the address, so the reference cannot leak an
            # address into provider logs or bounce reports.
            headers={
                **_standard_headers(f"notification-{notification_id}"),
                # RFC 8058. Both headers are required for one-click to be offered: the URL
                # alone leaves providers guessing whether a POST is safe, and a `mailto:`-only
                # header makes Gmail render nothing.
                "List-Unsubscribe": f"<{one_click_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
    except EmailProviderError as exc:
        return EmailOutcome(
            accepted=False,
            provider=exc.provider,
            provider_message_id=None,
            retryable=exc.retryable,
            error_code=f"{exc.provider.upper()}_{exc.status_code or 'ERROR'}",
            error_detail=str(exc)[:500],
            duration_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        # An unclassified failure is treated as transient: a network reset must not burn a
        # learner's reminder, and the attempt cap still bounds how long this can go on.
        logger.warning("Notification email failed for %s: %s", notification_id, exc)
        return EmailOutcome(
            accepted=False,
            provider=None,
            provider_message_id=None,
            retryable=True,
            error_code=type(exc).__name__,
            error_detail=str(exc)[:500],
            duration_ms=_elapsed_ms(started),
        )

    return EmailOutcome(
        accepted=True,
        provider=provider,
        provider_message_id=message_id,
        retryable=False,
        error_code=None,
        error_detail=None,
        duration_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)
