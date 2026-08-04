"""Identity-domain exceptions with stable client-facing error codes."""

from fastapi import status

from src.shared.exceptions import MaigieError


class EmailVerificationRequiredError(MaigieError):
    """The credentials are valid, but the email address is not verified."""

    def __init__(self) -> None:
        super().__init__(
            message="Account inactive. Please verify your email.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="EMAIL_VERIFICATION_REQUIRED",
        )
