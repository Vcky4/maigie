"""
Email sending service — auth transactional emails (OTP, welcome, password reset).

Bodies come from the Jinja templates in ``src/templates/email`` (``verification``,
``welcome``, ``reset_password``), which all extend the shared ``base.html`` layout.
This module used to build its own inline HTML and ignore those templates, so auth
mail looked nothing like the rest of the product's email.

Delivery goes through ``shared.infrastructure.email``, which walks the providers in
``EMAIL_OUTBOUND_STRATEGY`` order (SMTP, then the Resend HTTP API by default). Sending
directly over SMTP, as this module also used to do, meant a rejection — bad Brevo or
Gmail credentials, or a monthly quota — dropped the OTP even with a working Resend key.

Gracefully skips when no provider is configured (local dev).
"""

import logging

from src.config import get_settings
from src.shared.infrastructure.email import send_templated_email

logger = logging.getLogger(__name__)

# Kept in step with the OTP lifetime set in domains/identity/services.py.
OTP_EXPIRY_MINUTES = 15


def _login_url() -> str:
    settings = get_settings()
    base = settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200"
    return f"{base.rstrip('/')}/login"


async def send_verification_email(email: str, otp_code: str, name: str | None = None) -> None:
    """Send the signup OTP."""
    await send_templated_email(
        "verification",
        to_email=email,
        subject=f"Your Maigie verification code is {otp_code}",
        fallback_text=(
            f"Your Maigie verification code is {otp_code}. "
            f"It expires in {OTP_EXPIRY_MINUTES} minutes.\n"
            "If you didn't create a Maigie account, you can ignore this email."
        ),
        ref_id=f"verify-{email}",
        name=name,
        code=otp_code,
        expires_minutes=OTP_EXPIRY_MINUTES,
    )


async def send_welcome_email(email: str, name: str | None = None) -> None:
    """Send the welcome email after verification."""
    await send_templated_email(
        "welcome",
        to_email=email,
        subject="Welcome to Maigie",
        fallback_text=f"Your Maigie account is verified. Open Maigie: {_login_url()}",
        ref_id=f"welcome-{email}",
        name=name,
        login_url=_login_url(),
    )


async def send_password_reset_email(email: str, otp_code: str, name: str | None = None) -> None:
    """Send the password reset OTP."""
    await send_templated_email(
        "reset_password",
        to_email=email,
        subject="Reset your Maigie password",
        fallback_text=(
            f"Your Maigie password reset code is {otp_code}. "
            f"It expires in {OTP_EXPIRY_MINUTES} minutes.\n"
            "If you didn't request this, you can ignore this email."
        ),
        ref_id=f"password-reset-{email}",
        name=name,
        code=otp_code,
        expires_minutes=OTP_EXPIRY_MINUTES,
    )


async def send_template_email(to_email: str, template: str, context: dict) -> None:
    """Send a generic notification.

    ``template`` names the caller's notification type, not a file in the template
    folder: the worker that calls this passes values like ``"streak_reminder"``.
    The body is rendered through ``bulk_email``, which wraps caller-supplied text
    in the shared layout.
    """
    subject = context.get("subject", "Notification from Maigie")
    body = context.get("body", "You have a new notification from Maigie.")

    await send_templated_email(
        "bulk_email",
        to_email=to_email,
        subject=subject,
        fallback_text=body,
        ref_id=f"{template}-{to_email}",
        name=context.get("name"),
        content=body,
    )


__all__ = [
    "send_verification_email",
    "send_welcome_email",
    "send_password_reset_email",
    "send_template_email",
]
