"""
Email sending service — transactional emails via SMTP (Brevo) + Resend fallback.

This is shared infrastructure used by multiple domains (identity, billing, progress).
It wraps the existing email implementation during migration.

Usage:
    from src.integrations.brevo import send_verification_email, send_welcome_email

    await send_verification_email("user@example.com", "123456")
"""

import logging

logger = logging.getLogger(__name__)


async def send_verification_email(email: str, otp_code: str) -> None:
    """Send OTP verification email."""
    from src.services.email import send_verification_email as _send

    await _send(email, otp_code)


async def send_welcome_email(email: str, name: str | None = None) -> None:
    """Send welcome email after verification."""
    from src.services.email import send_welcome_email as _send

    await _send(email, name)


async def send_password_reset_email(email: str, otp_code: str, name: str | None = None) -> None:
    """Send password reset OTP email."""
    from src.services.email import send_password_reset_email as _send

    await _send(email, otp_code, name)


async def send_template_email(to_email: str, template: str, context: dict) -> None:
    """Send a templated email (generic)."""
    from src.services.email import send_generic_template_email as _send

    await _send(to_email, template, context)
