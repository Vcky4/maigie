"""
FastAPI exception handlers — registered globally on the app.

Converts MaigieError exceptions into structured JSON responses.
"""

import logging
import traceback

import sentry_sdk
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.config import get_settings

from .base import MaigieError

logger = logging.getLogger(__name__)


async def maigie_error_handler(request: Request, exc: MaigieError) -> JSONResponse:
    """Handle all MaigieError exceptions with structured responses."""
    settings = get_settings()
    is_server_error = exc.status_code >= 500

    if is_server_error:
        logger.error(
            f"MaigieError [{exc.status_code}]: {exc.code} - {exc.message}",
            exc_info=True,
            extra={"error_code": exc.code, "path": request.url.path},
        )
        sentry_sdk.capture_exception(exc)
    else:
        logger.warning(
            f"MaigieError [{exc.status_code}]: {exc.code} - {exc.message}",
            extra={"error_code": exc.code, "path": request.url.path},
        )

    body: dict = {
        "status_code": exc.status_code,
        "code": exc.code,
        "message": exc.message,
    }
    if settings.DEBUG and exc.detail:
        body["detail"] = exc.detail

    return JSONResponse(status_code=exc.status_code, content=body)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic/FastAPI validation errors."""
    settings = get_settings()
    errors = exc.errors()

    if len(errors) == 1:
        field = " -> ".join(str(loc) for loc in errors[0]["loc"])
        message = f"Validation error in '{field}': {errors[0]['msg']}"
    else:
        message = f"Request validation failed with {len(errors)} error(s)"

    body: dict = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "code": "VALIDATION_ERROR",
        "message": message,
    }
    if settings.DEBUG:
        body["detail"] = str(errors)

    logger.info(f"Validation error: {message}", extra={"path": request.url.path})
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected exceptions."""
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {exc}",
        exc_info=True,
        extra={"path": request.url.path, "traceback": traceback.format_exc()},
    )
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status_code": 500,
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred. Please try again later.",
        },
    )
