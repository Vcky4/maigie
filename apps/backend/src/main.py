"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .core.cache import cache
from .core.database import db
from .dependencies import SettingsDep
from .exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
)
from .middleware import LoggingMiddleware, SecurityHeadersMiddleware
from .routes.auth import router as auth_router


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

    yield

    # Shutdown
    print("Shutting down...")
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

    # Ready check endpoint (includes database and cache status)
    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Readiness check endpoint."""
        db_status = await db.health_check()
        cache_status = await cache.health_check()

        return {
            "status": "ready",
            "database": db_status,
            "cache": cache_status,
        }

    # Include routers
    app.include_router(auth_router)

    return app


# Create app instance
app = create_app()
