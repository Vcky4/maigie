import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

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

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)

        if use_tls and not use_ssl:
            server.starttls()

        if conf.USE_CREDENTIALS:
            server.login(smtp_user, smtp_password)

        server.send_message(multipart_msg)
        server.quit()
    except Exception as e:
        logger.error(f"SMTP error sending email to {to_email}: {e}")
        raise


async def _send_multipart_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
):
    """
    Async wrapper for sending multipart emails.
    """
    await asyncio.to_thread(
        _send_multipart_email_sync,
        to_email,
        subject,
        html_body,
        text_body,
        headers,
    )


async def send_verification_email(email: EmailStr, otp: str):
    """
    Sends a 6-digit OTP code to the user.
    """
    if not settings.SMTP_HOST:
        logger.warning(
            f"SMTP not configured. Mocking verification email to {email} with OTP: {otp}"
        )
        return

    template_data = {"code": otp, "app_name": "Maigie"}

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
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Skipping welcome email to {email}")
        return

    login_url = f"{settings.FRONTEND_BASE_URL}/login"
    template_data = {"name": name, "login_url": login_url, "app_name": "Maigie"}

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
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Mocking reset email to {email} with OTP: {otp}")
        return

    template_data = {"code": otp, "name": name, "app_name": "Maigie"}

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
