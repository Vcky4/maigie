import logging
from pathlib import Path
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from pydantic import EmailStr
from src.config import settings

logger = logging.getLogger(__name__)
# src/services/email.py

# ... imports ...

# define where templates are stored
TEMPLATE_FOLDER = Path(__file__).parent.parent / "templates" / "email"

# FIX: Use 'or' to provide dummy values if settings are None (like in CI/Testing)
conf = ConnectionConfig(
    MAIL_USERNAME=settings.SMTP_USER or "mock_user",
    MAIL_PASSWORD=settings.SMTP_PASSWORD or "mock_password",
    MAIL_FROM=settings.EMAILS_FROM_EMAIL or "noreply@maigie.com",
    MAIL_PORT=settings.SMTP_PORT or 587,
    MAIL_SERVER=settings.SMTP_HOST or "localhost",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    TEMPLATE_FOLDER=TEMPLATE_FOLDER,
)

# ... rest of the function ...


async def send_welcome_email(email: EmailStr, name: str):
    """
    Sends the official welcome email after successful verification.
    """
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Skipping welcome email to {email}")
        return

    # Link to your frontend login page
    login_url = f"{settings.FRONTEND_BASE_URL}/login"

    message = MessageSchema(
        subject="You're in! Welcome to Maigie",
        recipients=[email],
        template_body={"name": name, "login_url": login_url, "app_name": "Maigie"},
        subtype=MessageType.html,
    )

    fm = FastMail(conf)

    try:
        await fm.send_message(message, template_name="welcome.html")
        logger.info(f"Welcome email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")


async def send_verification_email(email: EmailStr, otp: str):
    """
    Sends a 6-digit OTP code to the user.
    """
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Mocking email to {email} with OTP: {otp}")
        return

    # We pass the 'otp' directly to the template
    message = MessageSchema(
        subject="Your Maigie Verification Code",
        recipients=[email],
        # variable name 'code' must match what is in your HTML file {{ code }}
        template_body={"code": otp, "app_name": "Maigie"},
        subtype=MessageType.html,
    )

    fm = FastMail(conf)

    try:
        await fm.send_message(message, template_name="verification.html")
        logger.info(f"Verification email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
