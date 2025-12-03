import logging
from pathlib import Path
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from pydantic import EmailStr
from src.config import settings

logger = logging.getLogger(__name__)

# define where templates are stored
TEMPLATE_FOLDER = Path(__file__).parent.parent / "templates" / "email"

# CI/CD Fix: Use 'or' to provide dummy values if settings are None (like in GitHub Actions)
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
    TEMPLATE_FOLDER=TEMPLATE_FOLDER
)


async def send_verification_email(email: EmailStr, otp: str):
    """
    Sends a 6-digit OTP code to the user for account verification.
    """
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Mocking verification email to {email} with OTP: {otp}")
        return

    message = MessageSchema(
        subject="Welcome to Maigie!",
        recipients=[email],
        template_body={"code": otp, "app_name": "Maigie"},
        subtype=MessageType.html
    )

    fm = FastMail(conf)
    
    try:
        await fm.send_message(message, template_name="verification.html")
        logger.info(f"Verification email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


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
        template_body={
            "name": name, 
            "login_url": login_url,
            "app_name": "Maigie"
        },
        subtype=MessageType.html
    )

    fm = FastMail(conf)
    
    try:
        await fm.send_message(message, template_name="welcome.html")
        logger.info(f"Welcome email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")


async def send_password_reset_email(email: EmailStr, otp: str, name: str):
    """
    Sends the password reset OTP code.
    """
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP not configured. Mocking reset email to {email} with OTP: {otp}")
        return

    message = MessageSchema(
        subject="Reset Your Maigie Password",
        recipients=[email],
        template_body={"code": otp, "name": name, "app_name": "Maigie"},
        subtype=MessageType.html
    )

    fm = FastMail(conf)
    
    try:
        await fm.send_message(message, template_name="reset_password.html")
        logger.info(f"Password reset email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send reset email: {e}")