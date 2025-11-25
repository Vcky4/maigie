# apps/backend/src/main.py
# Maigie - AI-powered student companion
# Copyright (C) 2025 Maigie

"""
FastAPI application entry point.

Copyright (C) 2024 Maigie Team

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .core.cache import cache
from .utils.logging_config import configure_logging

# --- Import the database helper functions ---
from src.core.database import connect_db, disconnect_db, check_db_health

from .core.websocket import manager as websocket_manager
from .dependencies import SettingsDep
from .exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
)
from .middleware import LoggingMiddleware, SecurityHeadersMiddleware
from .models.error_response import ErrorResponse
from .utils.dependencies import (
    cleanup_db_client,
    close_redis_client,
    get_db_client,
    get_redis_client,
    initialize_redis_client,
)
from .utils.exceptions import InternalServerError, MaigieError
from .routes.ai import router as ai_router
from .routes.auth import router as auth_router
from .routes.courses import router as courses_router
from .routes.examples import router as examples_router
from .routes.goals import router as goals_router
from .routes.realtime import router as realtime_router
from .routes.resources import router as resources_router
from .routes.schedule import router as schedule_router

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================================
# Global Exception Handlers
# ============================================================================

async def maigie_error_handler(request: Request, exc: MaigieError) -> JSONResponse:
    """
    Global exception handler for all MaigieError exceptions.
    
    Converts MaigieError instances into standardized ErrorResponse format.
    This ensures consistent error responses across the entire application.
    
    Args:
        request: The incoming request
        exc: The MaigieError exception
        
    Returns:
        JSONResponse with standardized error format
    """
    settings = get_settings()
    
    # Determine if this is a 500-level error (critical)
    is_server_error = exc.status_code >= 500
    
    # Log with appropriate level and full context
    log_context = {
        "error_code": exc.code,
        "status_code": exc.status_code,
        "detail": exc.detail,
        "path": request.url.path,
        "method": request.method,
        "user_agent": request.headers.get("user-agent"),
    }
    
    if is_server_error:
        # For 500-level errors, log at ERROR level with full traceback
        logger.error(
            f"MaigieError [500-level]: {exc.code} - {exc.message}",
            exc_info=True,
            extra={
                **log_context,
                "traceback": traceback.format_exc(),
            }
        )
        
        # Capture to Sentry for 500-level errors
        sentry_sdk.capture_exception(exc)
    else:
        # For 4xx errors, log at WARNING level
        logger.warning(
            f"MaigieError: {exc.code} - {exc.message}",
            extra=log_context
        )
    
    # Create error response
    error_response = ErrorResponse(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        detail=exc.detail if settings.DEBUG else None,  # Hide details in production
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(exclude_none=True),
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    """
    Global exception handler for FastAPI/Pydantic validation errors.
    
    Reformats RequestValidationError into standardized ErrorResponse format.
    Provides clear, user-friendly messages for validation failures.
    
    Args:
        request: The incoming request
        exc: The validation error
        
    Returns:
        JSONResponse with standardized error format
    """
    settings = get_settings()
    
    # Extract validation error details
    errors = exc.errors()
    
    # Create user-friendly message
    if len(errors) == 1:
        error = errors[0]
        field = " -> ".join(str(loc) for loc in error["loc"])
        message = f"Validation error in field '{field}': {error['msg']}"
    else:
        message = f"Request validation failed with {len(errors)} error(s)"
    
    # Format details
    detail = None
    if settings.DEBUG:
        detail = str(errors)
    
    error_response = ErrorResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="VALIDATION_ERROR",
        message=message,
        detail=detail,
    )
    
    logger.info(
        f"Validation error: {message}",
        extra={
            "errors": errors,
            "path": request.url.path,
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_response.model_dump(exclude_none=True),
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """
    Global exception handler for all unhandled exceptions.
    
    This is the safety net that catches any unexpected errors.
    Logs the full traceback for debugging but returns a generic
    error to the client to avoid leaking internal implementation details.
    
    Args:
        request: The incoming request
        exc: The unhandled exception
        
    Returns:
        JSONResponse with generic error message
    """
    # Build comprehensive log context
    log_context = {
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "path": request.url.path,
        "method": request.method,
        "user_agent": request.headers.get("user-agent"),
        "traceback": traceback.format_exc(),
    }
    
    # Log the full traceback at ERROR level with structured data
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {str(exc)}",
        exc_info=True,
        extra=log_context
    )
    
    # Capture to Sentry for all unhandled exceptions
    sentry_sdk.capture_exception(exc)
    
    # Create generic error response (don't leak internal details)
    error_response = ErrorResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An internal server error occurred. Please try again later.",
        detail=None,  # Never expose internal details to clients
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
    # Initialize structured logging FIRST (before any logging occurs)
    configure_logging()
    
    # Startup
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    
    # Initialize Sentry error tracking
    sentry_dsn = settings.SENTRY_DSN
    if sentry_dsn and sentry_dsn.strip():  # Check if DSN is not empty
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=settings.ENVIRONMENT,
            # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            # Send 100% of errors by default
            sample_rate=1.0,
            # Integrations
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.INFO,  # Capture info and above as breadcrumbs
                    event_level=logging.ERROR  # Send errors and above as events
                ),
            ],
            # Additional metadata
            release=settings.APP_VERSION,
        )
        logger.info(
            "Sentry error tracking initialized",
            extra={
                "environment": settings.ENVIRONMENT,
                "release": settings.APP_VERSION,
            }
        )
    else:
        # Check if .env file exists to give better error message
        env_file_path = Path(__file__).parent.parent / ".env"
        env_file_exists = env_file_path.exists()
        
        logger.warning(
            "Sentry DSN not configured - error tracking disabled",
            extra={
                "hint": "Set SENTRY_DSN in .env file or environment variable to enable",
                "env_file_path": str(env_file_path),
                "env_file_exists": env_file_exists,
            }
        )

    # Connect to database (legacy placeholder - kept for compatibility)
    await connect_db()
    logger.info("Legacy database connection initialized")

    # Connect to cache (legacy placeholder - kept for compatibility)
    await cache.connect()
    logger.info("Legacy cache connection initialized")
    
    # Initialize new dependency injection system
    # Prisma client will be initialized on first use via get_db_client()
    
    # Initialize Redis client for dependency injection
    await initialize_redis_client()
    logger.info("Redis client initialized for dependency injection")

    # --- WebSocket Manager ---
    settings = get_settings()
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

    # Disconnect all WebSocket connections
    connection_count = len(websocket_manager.active_connections)
    for connection_id in list(websocket_manager.active_connections.keys()):
        await websocket_manager.disconnect(connection_id, reason="server_shutdown")
    logger.info(f"Disconnected {connection_count} WebSocket connection(s)")
    
    # Cleanup new dependency injection clients
    await cleanup_db_client()
    await close_redis_client()
    logger.info("Dependency injection clients cleaned up")
    
    # Cleanup legacy connections
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

    # Add exception handlers (new standardized handlers)
    app.add_exception_handler(MaigieError, maigie_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    
    # Legacy exception handlers (for backward compatibility)
    app.add_exception_handler(AppException, app_exception_handler)

    # Add middleware
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LoggingMiddleware)

    # CORS middleware
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
        """Root endpoint."""
        if settings is None:
            settings = get_settings()
        return {
            "message": settings.APP_NAME,
            "version": settings.APP_VERSION,
        }

    # Multi-service health check endpoint
    @app.get("/health")
    async def health(
        db_client: Annotated[Any, Depends(get_db_client)],
        redis_client: Annotated[Any, Depends(get_redis_client)],
    ) -> dict[str, str]:
        """
        Multi-service health check endpoint.
        
        Validates connectivity to critical external services:
        - PostgreSQL database (via Prisma)
        - Redis cache
        
        Returns 200 OK if all services are connected, otherwise raises HTTPException.
        """
        from fastapi import HTTPException, status
        
        db_status = "disconnected"
        cache_status = "disconnected"
        errors = []
        
        # Test PostgreSQL/Prisma connectivity with a simple query
        try:
            # Use a simple SELECT 1 query to test database connectivity
            # This is the standard way to verify database connection
            result = await db_client.query_raw("SELECT 1 as test")
            
            # Verify we got a result back
            if result and len(result) > 0:
                db_status = "connected"
            else:
                errors.append("Database error: No response from database")
        except Exception as e:
            errors.append(f"Database error: {str(e)}")
        
        # Test Redis connectivity
        try:
            await redis_client.ping()
            cache_status = "connected"
        except Exception as e:
            errors.append(f"Cache error: {str(e)}")
        
        # Return error if any service is down
        if db_status != "connected" or cache_status != "connected":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "unhealthy",
                    "db": db_status,
                    "cache": cache_status,
                    "errors": errors,
                },
            )
        
        return {
            "status": "OK",
            "db": db_status,
            "cache": cache_status,
        }

    # Ready check endpoint
    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Readiness check endpoint."""
        db_status = await check_db_health()
        cache_status = await cache.health_check()

        return {
            "status": "ready",
            "database": db_status,
            "cache": cache_status,
        }

    # Include routers
    # Authentication router
    app.include_router(auth_router)
    
    # Core API routers
    app.include_router(ai_router)
    app.include_router(courses_router)
    app.include_router(goals_router)
    app.include_router(schedule_router)
    app.include_router(resources_router)
    
    # Real-time communication router
    app.include_router(realtime_router)
    
    # Example/demonstration endpoints
    app.include_router(examples_router)

    return app


# Create app instance
app = create_app()
