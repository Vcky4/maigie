"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .core.cache import cache
from .core.celery_app import celery_app
from .core.database import db
from .core.websocket import manager as websocket_manager
from .workers.manager import check_worker_health
from .dependencies import SettingsDep
from .exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
)
from .middleware import LoggingMiddleware, SecurityHeadersMiddleware
from .routes.auth import router as auth_router
from .routes.realtime import router as realtime_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = get_settings()
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Connect to database (placeholder for now)
    await db.connect()
    print("Database connection initialized")

    # Connect to cache (placeholder for now)
    await cache.connect()
    print("Cache connection initialized")

    # Initialize WebSocket manager
    settings = get_settings()
    websocket_manager.heartbeat_interval = settings.WEBSOCKET_HEARTBEAT_INTERVAL
    websocket_manager.heartbeat_timeout = settings.WEBSOCKET_HEARTBEAT_TIMEOUT
    websocket_manager.max_reconnect_attempts = settings.WEBSOCKET_MAX_RECONNECT_ATTEMPTS
    await websocket_manager.start_heartbeat()
    await websocket_manager.start_cleanup()
    print("WebSocket manager initialized")

    yield

    # Shutdown
    print("Shutting down...")
    await websocket_manager.stop_heartbeat()
    await websocket_manager.stop_cleanup()
    # Disconnect all WebSocket connections
    for connection_id in list(websocket_manager.active_connections.keys()):
        await websocket_manager.disconnect(connection_id, reason="server_shutdown")
    await cache.disconnect()
    await db.disconnect()
    print("Shutdown complete")


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

    # Add exception handlers
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # Add middleware (order matters - last added is first executed)
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

    # Health check endpoint
    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    # Ready check endpoint (includes database, cache, and worker status)
    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Readiness check endpoint."""
        db_status = await db.health_check()
        cache_status = await cache.health_check()
        worker_status = await check_worker_health()

        return {
            "status": "ready",
            "database": db_status,
            "cache": cache_status,
            "workers": worker_status,
        }

    # Include routers
    app.include_router(auth_router)
    app.include_router(realtime_router)

    return app


# Create app instance
app = create_app()
