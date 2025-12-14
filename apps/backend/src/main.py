# apps/backend/src/main.py
# apps/backend/src/main.py
# Maigie - AI-powered student companion
# Copyright (C) 2025 Maigie

"""
FastAPI application entry point.
Copyright (C) 2025 Maigie
"""

import logging
import traceback
from contextlib import asynccontextmanager
from typing import Annotated, Any

import sentry_sdk
from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import BadDsn
from starlette.middleware.sessions import SessionMiddleware

# --- Import the database helper functions ---
from src.core.database import check_db_health, connect_db, disconnect_db

from .config import get_settings
from .core.cache import cache
from .core.websocket import manager as websocket_manager
from .dependencies import SettingsDep
from .exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
)
from .middleware import LoggingMiddleware, SecurityHeadersMiddleware
from .models.error_response import ErrorResponse

# --- Route Imports ---
from .routes.ai import router as ai_router
from .routes.auth import router as auth_router
from .routes.users import router as users_router
from .routes.courses import router as courses_router
from .routes.examples import router as examples_router
from .routes.goals import router as goals_router
from .routes.realtime import router as realtime_router
from .routes.resources import router as resources_router
from .routes.schedule import router as schedule_router
from .routes.subscriptions import router as subscriptions_router
from .routes.stripe_webhook import router as stripe_webhook_router
from .routes.waitlist import router as waitlist_router
from .utils.dependencies import (
    cleanup_db_client,
    close_redis_client,
    get_db_client,
    get_redis_client,
    initialize_redis_client,
)
from .utils.exceptions import InternalServerError, MaigieError
from .utils.logging_config import configure_logging
from .workers.manager import check_worker_health

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================================
# Global Exception Handlers
# ============================================================================


async def maigie_error_handler(request: Request, exc: MaigieError) -> JSONResponse:
    """Global exception handler for all MaigieError exceptions."""
    settings = get_settings()
    is_server_error = exc.status_code >= 500

    log_context = {
        "error_code": exc.code,
        "status_code": exc.status_code,
        "detail": exc.detail,
        "path": request.url.path,
        "method": request.method,
        "user_agent": request.headers.get("user-agent"),
    }

    if is_server_error:
        logger.error(
            f"MaigieError [500-level]: {exc.code} - {exc.message}",
            exc_info=True,
            extra={**log_context, "traceback": traceback.format_exc()},
        )
        sentry_sdk.capture_exception(exc)
    else:
        logger.warning(f"MaigieError: {exc.code} - {exc.message}", extra=log_context)

    error_response = ErrorResponse(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        detail=exc.detail if settings.DEBUG else None,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(exclude_none=True),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Global exception handler for FastAPI/Pydantic validation errors."""
    settings = get_settings()
    errors = exc.errors()

    if len(errors) == 1:
        error = errors[0]
        field = " -> ".join(str(loc) for loc in error["loc"])
        message = f"Validation error in field '{field}': {error['msg']}"
    else:
        message = f"Request validation failed with {len(errors)} error(s)"

    detail = str(errors) if settings.DEBUG else None

    error_response = ErrorResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="VALIDATION_ERROR",
        message=message,
        detail=detail,
    )

    logger.info(f"Validation error: {message}", extra={"errors": errors, "path": request.url.path})

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_response.model_dump(exclude_none=True),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for all unhandled exceptions."""
    log_context = {
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "path": request.url.path,
        "method": request.method,
        "user_agent": request.headers.get("user-agent"),
        "traceback": traceback.format_exc(),
    }

    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {str(exc)}", exc_info=True, extra=log_context
    )
    sentry_sdk.capture_exception(exc)

    error_response = ErrorResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An internal server error occurred. Please try again later.",
        detail=None,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.model_dump(exclude_none=True),
    )


# ============================================================================
# Application Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    configure_logging()
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Initialize Sentry
    sentry_dsn = settings.SENTRY_DSN
    if sentry_dsn and sentry_dsn.strip():
        # Check for placeholder values that indicate DSN is not configured
        placeholder_values = ["project-id", "your-project-id", "placeholder", "xxx", "your-dsn"]
        dsn_lower = sentry_dsn.lower()
        is_placeholder = any(placeholder in dsn_lower for placeholder in placeholder_values)

        if is_placeholder:
            logger.warning("Sentry DSN appears to be a placeholder - error tracking disabled")
        else:
            try:
                sentry_sdk.init(
                    dsn=sentry_dsn,
                    environment=settings.ENVIRONMENT,
                    traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
                    sample_rate=1.0,
                    integrations=[
                        FastApiIntegration(transaction_style="endpoint"),
                        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
                    ],
                    release=settings.APP_VERSION,
                )
                logger.info("Sentry error tracking initialized")
            except BadDsn as e:
                logger.warning(f"Invalid Sentry DSN (non-critical): {e}. Error tracking disabled.")
            except Exception as e:
                logger.warning(
                    f"Failed to initialize Sentry (non-critical): {e}. Error tracking disabled."
                )
    else:
        logger.warning("Sentry DSN not configured - error tracking disabled")

    # Connect to database
    await connect_db()
    logger.info("Database connection initialized")

    # Connect to cache
    await cache.connect()
    logger.info("Cache connection initialized")

    # Initialize Redis for DI
    await initialize_redis_client()
    logger.info("Redis client initialized for dependency injection")

    # Initialize WebSocket
    websocket_manager.heartbeat_interval = settings.WEBSOCKET_HEARTBEAT_INTERVAL
    websocket_manager.heartbeat_timeout = settings.WEBSOCKET_HEARTBEAT_TIMEOUT
    websocket_manager.max_reconnect_attempts = settings.WEBSOCKET_MAX_RECONNECT_ATTEMPTS
    await websocket_manager.start_heartbeat()
    await websocket_manager.start_cleanup()
    logger.info("WebSocket manager initialized")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down application...")
    await websocket_manager.stop_heartbeat()
    await websocket_manager.stop_cleanup()

    for connection_id in list(websocket_manager.active_connections.keys()):
        await websocket_manager.disconnect(connection_id, reason="server_shutdown")

    await cleanup_db_client()
    await close_redis_client()
    await cache.disconnect()
    await disconnect_db()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # Exception Handlers
    app.add_exception_handler(MaigieError, maigie_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(AppException, app_exception_handler)

    # Middleware
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LoggingMiddleware)

    # --- PLACEHOLDER FOR TEAMMATE (OAuth Session) ---
    # OAuth requires session middleware to store the 'state' parameter securely.
    # app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
    )

    # Root endpoint
    @app.get("/")
    async def root(settings: SettingsDep = None) -> dict[str, str]:
        if settings is None:
            settings = get_settings()
        return {"message": settings.APP_NAME, "version": settings.APP_VERSION}

    # Metrics
    @app.get("/metrics")
    async def metrics() -> Response:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # Health Check
    @app.get("/health")
    async def health(
        db_client: Annotated[Any, Depends(get_db_client)],
        redis_client: Annotated[Any, Depends(get_redis_client)],
    ) -> dict[str, str]:
        from fastapi import HTTPException, status

        # Check DB
        try:
            await db_client.query_raw("SELECT 1")
            db_status = "connected"
        except Exception:
            db_status = "disconnected"

        # Check Redis
        try:
            await redis_client.ping()
            cache_status = "connected"
        except Exception:
            cache_status = "disconnected"

        if db_status != "connected":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "unhealthy", "db": db_status, "cache": cache_status},
            )
        return {"status": "healthy", "db": db_status, "cache": cache_status}

    # Ready Check
    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        db_status = await check_db_health()
        cache_status = await cache.health_check()
        worker_status = await check_worker_health()
        return {
            "status": "ready",
            "database": db_status,
            "cache": cache_status,
            "workers": worker_status,
        }

    # --- REGISTER ROUTERS ---
    app.include_router(auth_router, prefix=f"{settings.API_V1_STR}/auth")
    app.include_router(users_router, prefix=f"{settings.API_V1_STR}/users")
    app.include_router(subscriptions_router, prefix=f"{settings.API_V1_STR}/subscriptions")

    app.include_router(ai_router)
    app.include_router(courses_router)
    app.include_router(goals_router)
    app.include_router(schedule_router)
    app.include_router(resources_router)
    app.include_router(realtime_router)

    # Webhook endpoints (no auth required, verified by Stripe signature)
    app.include_router(stripe_webhook_router, prefix=f"{settings.API_V1_STR}/webhooks")

    # Waitlist router
    app.include_router(waitlist_router)

    # Example/demonstration endpoints
    app.include_router(examples_router)

    return app


# Create app instance
app = create_app()
