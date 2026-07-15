"""Brevo integration — CRM and transactional email."""

from .email_service import (
    send_password_reset_email,
    send_template_email,
    send_verification_email,
    send_welcome_email,
)

__all__ = [
    "send_verification_email",
    "send_welcome_email",
    "send_password_reset_email",
    "send_template_email",
]
