import asyncio
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from fastapi_mail import ConnectionConfig
from jinja2 import Environment, FileSystemLoader
from pydantic import EmailStr

from src.config import settings

logger = logging.getLogger(__name__)

# define where templates are stored
TEMPLATE_FOLDER = Path(__file__).parent.parent / "templates" / "email"

# Set up Jinja2 environment for rendering templates
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_FOLDER)))

# FIX: Use 'or' to provide dummy values if settings are None (like in CI/Testing)
conf = ConnectionConfig(
    MAIL_USERNAME=settings.SMTP_USER or "mock_user",
    MAIL_PASSWORD=settings.SMTP_PASSWORD or "mock_password",
    MAIL_FROM=settings.EMAILS_FROM_EMAIL or "noreply@maigie.com",
    MAIL_FROM_NAME=settings.EMAILS_FROM_NAME or "Maigie",
    MAIL_PORT=settings.SMTP_PORT or 587,
    MAIL_SERVER=settings.SMTP_HOST or "localhost",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    TEMPLATE_FOLDER=TEMPLATE_FOLDER,
)

# Get the from email address for Reply-To header
_from_email = settings.EMAILS_FROM_EMAIL or "noreply@maigie.com"

RESEND_API_URL = "https://api.resend.com/emails"

_VALID_OUTBOUND_STRATEGIES: dict[str, tuple[str, ...]] = {
    "smtp_then_resend": ("smtp", "resend"),
    "resend_then_smtp": ("resend", "smtp"),
    "resend_only": ("resend",),
    "smtp_only": ("smtp",),
}


def _email_transport_configured() -> bool:
    """True if we can send via SMTP and/or Resend fallback."""
    return bool(settings.SMTP_HOST) or bool(settings.RESEND_API_KEY)


def _outbound_provider_order() -> tuple[str, ...]:
    """Ordered providers for this send (smtp / resend), from EMAIL_OUTBOUND_STRATEGY."""
    raw = (settings.EMAIL_OUTBOUND_STRATEGY or "smtp_then_resend").strip().lower()
    return _VALID_OUTBOUND_STRATEGIES.get(raw, ("smtp", "resend"))


def _smtp_error_suggests_quota(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc!s}".lower()
    return any(
        needle in text
        for needle in (
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
    )


def _send_multipart_email_sync(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
):
    """
    Synchronous helper function to send multipart emails (HTML + Text).
    Uses SMTP directly for full control.
    """
    # Create multipart message
    multipart_msg = MIMEMultipart("alternative")
    multipart_msg["Subject"] = subject
    multipart_msg["To"] = to_email
    multipart_msg["From"] = f"{settings.EMAILS_FROM_NAME or 'Maigie'} <{_from_email}>"

    # Add custom headers
    if headers:
        for key, value in headers.items():
            multipart_msg[key] = value

    # Add plaintext part first (lower priority)
    text_part = MIMEText(text_body, "plain", "utf-8")
    multipart_msg.attach(text_part)

    # Add HTML part (higher priority)
    html_part = MIMEText(html_body, "html", "utf-8")
    multipart_msg.attach(html_part)

    # Send via SMTP
    smtp_host = settings.SMTP_HOST or "localhost"
    smtp_port = settings.SMTP_PORT or 587
    smtp_user = settings.SMTP_USER or "mock_user"
    smtp_password = settings.SMTP_PASSWORD or "mock_password"
    use_tls = conf.MAIL_STARTTLS
    use_ssl = conf.MAIL_SSL_TLS

    server: smtplib.SMTP | smtplib.SMTP_SSL | None = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)

        if use_tls and not use_ssl:
            server.starttls()

        if conf.USE_CREDENTIALS:
            server.login(smtp_user, smtp_password)

        # send_message returns dict of refused recipients without raising if only some fail
        refused = server.send_message(multipart_msg)
        if refused:
            raise smtplib.SMTPException(f"SMTP server refused recipient(s): {refused}")
    except Exception as e:
        if _smtp_error_suggests_quota(e):
            logger.warning(
                "SMTP error for %s may be provider quota/limit (will try next outbound provider if configured): %s",
                to_email,
                e,
            )
        logger.error(f"SMTP error sending email to {to_email}: {e}")
        raise
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


async def _send_via_resend(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> None:
    """Send HTML + text email through Resend HTTP API (fallback when SMTP fails)."""
    from_addr = settings.RESEND_FROM_EMAIL or settings.EMAILS_FROM_EMAIL or _from_email
    from_name = settings.EMAILS_FROM_NAME or "Maigie"
    payload: dict[str, object] = {
        "from": f"{from_name} <{from_addr}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    extra_headers: dict[str, str] = {}
    if headers:
        for key, value in headers.items():
            lk = key.lower()
            if lk == "reply-to":
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
            detail = response.json()
        except Exception:
            detail = response.text
        logger.error(
            "Resend API error sending to %s: HTTP %s %s",
            to_email,
            response.status_code,
            detail,
        )
        raise RuntimeError(f"Resend send failed with HTTP {response.status_code}") from None


async def _send_multipart_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
):
    """
    Send multipart email using EMAIL_OUTBOUND_STRATEGY (SMTP and/or Resend).

    Brevo (and similar) quota errors usually surface as SMTP exceptions or refused
    recipients; those trigger the next provider in the chain. If SMTP returns OK
    but the provider drops mail silently, switch strategy to resend_then_smtp or
    resend_only until the quota resets.
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
                logger.info(
                    "Outbound email delivered via=smtp to=%s subject=%r tried=%s",
                    to_email,
                    subject,
                    tried,
                )
                return
            except Exception as e:
                last_error = e
                tried.append("smtp")
                logger.warning(
                    "Outbound smtp failed to=%s subject=%r: %s",
                    to_email,
                    subject,
                    e,
                )
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
                logger.info(
                    "Outbound email delivered via=resend to=%s subject=%r tried=%s",
                    to_email,
                    subject,
                    tried,
                )
                return
            except Exception as e:
                last_error = e
                tried.append("resend")
                logger.warning(
                    "Outbound resend failed to=%s subject=%r: %s",
                    to_email,
                    subject,
                    e,
                )

    if last_error is not None:
        raise last_error
    raise RuntimeError(
        "No usable outbound email provider for this strategy "
        f"(chain={chain!s}, SMTP_HOST={'set' if settings.SMTP_HOST else 'unset'}, "
        f"RESEND_API_KEY={'set' if settings.RESEND_API_KEY else 'unset'})"
    )


async def send_verification_email(email: EmailStr, otp: str):
    """
    Sends a 6-digit OTP code to the user.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Mocking verification email to {email} with OTP: {otp}"
        )
        return

    template_data = {
        "code": otp,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    # Render templates
    html_template = jinja_env.get_template("verification.html")
    try:
        text_template = jinja_env.get_template("verification.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"Your verification code is: {otp}"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"verification-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject="Welcome to Maigie!",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Verification email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise


async def send_welcome_email(email: EmailStr, name: str):
    """
    Sends the official welcome email after successful verification.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping welcome email to {email}"
        )
        return

    login_url = f"{settings.FRONTEND_BASE_URL}/login"
    template_data = {
        "name": name,
        "login_url": login_url,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_template = jinja_env.get_template("welcome.html")
    try:
        text_template = jinja_env.get_template("welcome.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"Welcome to Maigie, {name}! You can now login at {login_url}"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"welcome-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject="You're in! Welcome to Maigie",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Welcome email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")
        raise


async def send_password_reset_email(email: EmailStr, otp: str, name: str):
    """
    Sends the password reset OTP code.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Mocking reset email to {email} with OTP: {otp}"
        )
        return

    template_data = {
        "code": otp,
        "name": name,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_template = jinja_env.get_template("reset_password.html")
    try:
        text_template = jinja_env.get_template("reset_password.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"Reset your password using this code: {otp}"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"reset-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject="Reset Your Maigie Password",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Password reset email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send reset email: {e}")
        raise


async def send_subscription_success_email(email: EmailStr, name: str, tier: str):
    """
    Sends email confirmation after successful subscription.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping subscription email to {email}"
        )
        return

    TIER_NAMES = {
        "PREMIUM_MONTHLY": "Maigie Plus Monthly",
        "PREMIUM_YEARLY": "Maigie Plus Yearly",
        "STUDY_CIRCLE_MONTHLY": "Study Circle Monthly",
        "STUDY_CIRCLE_YEARLY": "Study Circle Yearly",
        "SQUAD_MONTHLY": "Squad Plan Monthly",
        "SQUAD_YEARLY": "Squad Plan Yearly",
    }
    tier_name = TIER_NAMES.get(str(tier), str(tier).replace("_", " "))
    dashboard_url = f"{settings.FRONTEND_BASE_URL}/dashboard"

    template_data = {
        "name": name,
        "tier_name": tier_name,
        "dashboard_url": dashboard_url,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_template = jinja_env.get_template("subscription_success.html")
    try:
        text_template = jinja_env.get_template("subscription_success.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"Thank you for subscribing to {tier_name}, {name}!"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"subscription-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject="Subscription Confirmed!",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Subscription success email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send subscription email: {e}")
        # Don't raise here, as subscription was successful


async def send_bulk_email(
    email: EmailStr,
    name: str | None,
    subject: str,
    content: str,
):
    """
    Sends a bulk email to a user using the generic bulk email template.

    Args:
        email: Recipient email address
        name: Recipient name (optional)
        subject: Email subject line
        content: HTML content for the email body
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping bulk email to {email}"
        )
        return

    if settings.ENVIRONMENT != "production":
        logger.info(
            f"Skipping bulk email to {email} (subject: {subject}) - ENVIRONMENT is {settings.ENVIRONMENT}"
        )
        return

    template_data = {
        "name": name,
        "subject": subject,
        "content": content,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    # Render templates
    html_template = jinja_env.get_template("bulk_email.html")
    try:
        text_template = jinja_env.get_template("bulk_email.txt")
        # Convert HTML content to plain text for text version
        # Simple conversion: remove HTML tags (basic implementation)
        text_content = re.sub(r"<[^>]+>", "", content)
        text_content = (
            text_content.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
        )
        template_data["content"] = text_content
        text_body = text_template.render(**template_data)
    except Exception:
        # Fallback: simple text version
        text_content = re.sub(r"<[^>]+>", "", content)
        text_body = f"{subject}\n\nHi {name or 'there'},\n\n{text_content}"

    # Reset content for HTML template
    template_data["content"] = content
    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"bulk-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Bulk email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send bulk email to {email}: {e}")
        raise


def _get_frontend_base_url() -> str:
    """Base URL for frontend links (schedule, dashboard)."""
    return (settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200").rstrip(
        "/"
    )


async def send_morning_schedule_email(
    email: EmailStr,
    name: str | None,
    subject: str,
    template_data: dict,
):
    """
    Sends the morning schedule digest email using the dedicated template.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping morning schedule email to {email}"
        )
        return

    if settings.ENVIRONMENT != "production":
        logger.info(
            f"Skipping morning schedule email to {email} - ENVIRONMENT is {settings.ENVIRONMENT}"
        )
        return

    td = dict(template_data)
    td.setdefault("schedule_heading", "Today's schedule")
    td.setdefault("upgrade_pitch_html", "")
    td.setdefault("upgrade_pitch_plain", "")

    base_data = {
        "name": name or "there",
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
        "schedule_url": f"{_get_frontend_base_url()}/schedule",
        **td,
    }

    html_template = jinja_env.get_template("morning_schedule.html")
    try:
        text_template = jinja_env.get_template("morning_schedule.txt")
        text_body = text_template.render(**base_data)
    except Exception:
        text_body = f"Your schedule for today. View at: {base_data['schedule_url']}"

    html_body = html_template.render(**base_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"morning-schedule-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Morning schedule email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send morning schedule email to {email}: {e}")
        raise


async def send_schedule_reminder_email(
    email: EmailStr,
    name: str | None,
    subject: str,
    template_data: dict,
):
    """Sends a schedule reminder email (15 minutes before start)."""
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping schedule reminder to {email}"
        )
        return

    if settings.ENVIRONMENT != "production":
        logger.info(
            f"Skipping schedule reminder email to {email} - ENVIRONMENT is {settings.ENVIRONMENT}"
        )
        return

    base_data = {
        "name": name or "there",
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
        "schedule_url": f"{_get_frontend_base_url()}/schedule",
        **template_data,
    }

    html_template = jinja_env.get_template("schedule_reminder.html")
    try:
        text_template = jinja_env.get_template("schedule_reminder.txt")
        text_body = text_template.render(**base_data)
    except Exception:
        text_body = f"Reminder: {template_data.get('schedule_title', 'Your session')} starts soon. {base_data['schedule_url']}"

    html_body = html_template.render(**base_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"schedule-reminder-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Schedule reminder email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send schedule reminder to {email}: {e}")
        raise


async def send_limit_reached_email(email: EmailStr, name: str | None):
    """
    Sends an email when user hits their monthly limit, encouraging them to start a free trial.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping limit reached email to {email}"
        )
        return

    subscription_url = f"{_get_frontend_base_url()}/settings?tab=subscription"
    template_data = {
        "name": name or "there",
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
        "subscription_url": subscription_url,
    }

    html_template = jinja_env.get_template("limit_reached.html")
    try:
        text_template = jinja_env.get_template("limit_reached.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"You've reached your monthly limit. Start a free trial: {subscription_url}"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"limit-reached-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject="You've reached your limit — Start a free trial",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Limit reached email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send limit reached email to {email}: {e}")
        raise


async def send_weekly_tips_email(
    email: EmailStr,
    name: str | None,
    subject: str,
    template_data: dict,
):
    """Sends the weekly encouragement/tips email."""
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping weekly tips email to {email}"
        )
        return

    if settings.ENVIRONMENT != "production":
        logger.info(
            f"Skipping weekly tips email to {email} - ENVIRONMENT is {settings.ENVIRONMENT}"
        )
        return

    base_data = {
        "name": name or "there",
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
        "dashboard_url": f"{_get_frontend_base_url()}/dashboard",
        **template_data,
    }

    html_template = jinja_env.get_template("weekly_tips.html")
    try:
        text_template = jinja_env.get_template("weekly_tips.txt")
        text_body = text_template.render(**base_data)
    except Exception:
        text_body = f"Weekly tips. Open Maigie: {base_data['dashboard_url']}"

    html_body = html_template.render(**base_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"weekly-tips-{email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(email),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Weekly tips email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send weekly tips to {email}: {e}")
        raise


async def send_circle_invite_email(to_email: str, inviter_name: str, circle_name: str):
    """
    Sends an email to a user when they are invited to a study circle.
    """
    if not _email_transport_configured():
        logger.warning(
            f"Outbound email not configured (SMTP_HOST or RESEND_API_KEY). Skipping circle invite email to {to_email}"
        )
        return

    circles_url = f"{_get_frontend_base_url()}/circles"
    template_data = {
        "inviter_name": inviter_name,
        "circle_name": circle_name,
        "circles_url": circles_url,
        "app_name": "Maigie",
        "logo_url": settings.EMAIL_LOGO_URL or "",
    }

    html_template = jinja_env.get_template("circle_invite.html")
    try:
        text_template = jinja_env.get_template("circle_invite.txt")
        text_body = text_template.render(**template_data)
    except Exception:
        text_body = f"{inviter_name} has invited you to join their study circle '{circle_name}' on Maigie. Join here: {circles_url}"

    html_body = html_template.render(**template_data)

    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"circle-invite-{to_email}",
    }

    try:
        await _send_multipart_email(
            to_email=str(to_email),
            subject=f"You're invited to join {circle_name} on Maigie",
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        logger.info(f"Circle invite email sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send circle invite email to {to_email}: {e}")
        # Don't raise here, as invite was created successfully


async def send_account_deletion_reminder_email(
    email: EmailStr,
    name: str | None,
    *,
    days_left: int,
    scheduled_for_iso: str,
    cancel_url: str,
):
    """
    Reminder before scheduled account deletion with cancellation link.
    """
    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping account deletion reminder to %s",
            email,
        )
        return

    subject = f"Your Maigie account is scheduled for deletion in {days_left} day(s)"
    safe_name = name or "there"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height:1.6; color:#0f172a;">
      <p>Hi {safe_name},</p>
      <p>
        Your Maigie account is currently scheduled for permanent deletion in
        <strong>{days_left} day(s)</strong>.
      </p>
      <p>Scheduled deletion date: <strong>{scheduled_for_iso}</strong></p>
      <p>
        If you want to keep your account, you can cancel this request now:
      </p>
      <p>
        <a href="{cancel_url}" style="display:inline-block;padding:10px 14px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:8px;">
          Cancel deletion
        </a>
      </p>
      <p>If you do nothing, the account and related data will be permanently deleted on the scheduled date.</p>
      <p>— Maigie Team</p>
    </div>
    """.strip()
    text_body = (
        f"Hi {safe_name},\n\n"
        f"Your Maigie account is scheduled for permanent deletion in {days_left} day(s).\n"
        f"Scheduled deletion date: {scheduled_for_iso}\n\n"
        f"To cancel deletion, open: {cancel_url}\n\n"
        "If you do nothing, the account and related data will be permanently deleted on the scheduled date.\n\n"
        "— Maigie Team"
    )
    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"account-delete-reminder-{email}",
    }
    await _send_multipart_email(
        to_email=str(email),
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        headers=headers,
    )


async def send_account_deleted_email(email: EmailStr, name: str | None):
    """
    Confirmation sent after account deletion is completed.
    """
    if not _email_transport_configured():
        logger.warning(
            "Outbound email not configured (SMTP_HOST or RESEND_API_KEY). "
            "Skipping account deleted confirmation to %s",
            email,
        )
        return

    safe_name = name or "there"
    subject = "Your Maigie account has been deleted"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height:1.6; color:#0f172a;">
      <p>Hi {safe_name},</p>
      <p>
        This confirms that your Maigie account and associated study data have been deleted.
      </p>
      <p>If this was not expected, contact us at <a href="mailto:privacy@maigie.com">privacy@maigie.com</a>.</p>
      <p>— Maigie Team</p>
    </div>
    """.strip()
    text_body = (
        f"Hi {safe_name},\n\n"
        "This confirms that your Maigie account and associated study data have been deleted.\n\n"
        "If this was not expected, contact privacy@maigie.com.\n\n"
        "— Maigie Team"
    )
    headers = {
        "Reply-To": _from_email,
        "X-Mailer": "Maigie API",
        "X-Entity-Ref-ID": f"account-deleted-{email}",
    }
    await _send_multipart_email(
        to_email=str(email),
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        headers=headers,
    )
