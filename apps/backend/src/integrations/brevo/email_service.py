"""
Email sending service — transactional emails via SMTP (Brevo).

Sends OTP verification, welcome, and password reset emails.
Gracefully skips when SMTP is not configured (local dev).
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


async def _send_smtp_email(to_email: str, subject: str, html_body: str) -> None:
    """Send an email via SMTP. Skips gracefully if SMTP not configured."""
    from src.config import get_settings

    settings = get_settings()

    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning(f"SMTP not configured — skipping email to {to_email}: {subject}")
        return

    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["From"] = (
            f"{settings.EMAILS_FROM_NAME or 'Maigie'} "
            f"<{settings.EMAILS_FROM_EMAIL or settings.SMTP_USER}>"
        )
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT or 587,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD or "",
            start_tls=True,
        )
        logger.info(f"Email sent to {to_email}: {subject}")
    except ImportError:
        logger.warning("aiosmtplib not installed — skipping email send")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise


async def send_verification_email(email: str, otp_code: str) -> None:
    """Send OTP verification email."""
    subject = "Verify your Maigie account"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Verify your email</h2>
        <p>Your verification code is:</p>
        <h1 style="letter-spacing: 8px; font-size: 36px; text-align: center;
                   background: #f5f5f5; padding: 20px; border-radius: 8px;">{otp_code}</h1>
        <p>This code expires in 15 minutes.</p>
        <p style="color: #666; font-size: 12px;">If you didn't create a Maigie account, you can safely ignore this email.</p>
    </div>
    """
    await _send_smtp_email(email, subject, html)


async def send_welcome_email(email: str, name: str | None = None) -> None:
    """Send welcome email after verification."""
    greeting = f"Hi {name}" if name else "Welcome"
    subject = "Welcome to Maigie!"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{greeting}, welcome to Maigie! 🎉</h2>
        <p>Your account is now active. You're ready to start your learning journey.</p>
        <p>Here's what you can do next:</p>
        <ul>
            <li>Set your learning purpose</li>
            <li>Create your first study notes</li>
            <li>Start preparing for an exam</li>
        </ul>
        <p>Happy learning!</p>
    </div>
    """
    await _send_smtp_email(email, subject, html)


async def send_password_reset_email(email: str, otp_code: str, name: str | None = None) -> None:
    """Send password reset OTP email."""
    greeting = f"Hi {name}" if name else "Hello"
    subject = "Reset your Maigie password"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{greeting},</h2>
        <p>You requested a password reset. Your code is:</p>
        <h1 style="letter-spacing: 8px; font-size: 36px; text-align: center;
                   background: #f5f5f5; padding: 20px; border-radius: 8px;">{otp_code}</h1>
        <p>This code expires in 15 minutes.</p>
        <p style="color: #666; font-size: 12px;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    await _send_smtp_email(email, subject, html)


async def send_template_email(to_email: str, template: str, context: dict) -> None:
    """Send a templated email (generic). Falls back to plain text."""
    subject = context.get("subject", "Notification from Maigie")
    body = context.get("body", "You have a new notification from Maigie.")
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{subject}</h2>
        <p>{body}</p>
    </div>
    """
    await _send_smtp_email(to_email, subject, html)
