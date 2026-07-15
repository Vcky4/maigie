"""
Maigie FastAPI application factory.

Creates and configures the application by assembling shared infrastructure
and domain routers. Each domain registers its own router — this file
only wires them together.

Usage:
    uvicorn src.app:app --reload

    Or programmatically:
    from src.app import create_app
    app = create_app()
"""

import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import BadDsn
from starlette.middleware.sessions import SessionMiddleware

from src.config import get_settings
from src.shared.database import connect_db, disconnect_db
from src.shared.exceptions import (
    MaigieError,
    maigie_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from src.shared.infrastructure import cache
from src.shared.middleware import LoggingMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # --- Sentry ---
    _init_sentry(settings)

    # --- Database ---
    await connect_db()
    logger.info("Database connected")

    # --- Redis / Cache ---
    await cache.connect()
    logger.info("Cache connected")

    yield  # Application runs

    # --- Shutdown ---
    logger.info("Shutting down...")
    await cache.disconnect()
    await disconnect_db()
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Maigie — Intelligent Learning Environment API",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # --- Exception Handlers ---
    app.add_exception_handler(MaigieError, maigie_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # --- Middleware (order matters: last added = first executed) ---
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
    )

    # --- Domain Routers ---
    _register_domains(app)

    # --- Health Check ---
    @app.get("/health", tags=["system"])
    async def health_check():
        """Basic health check endpoint."""
        from src.shared.database import check_db_health

        db_health = await check_db_health()
        cache_health = await cache.health_check()
        return {
            "status": "healthy" if db_health["status"] == "healthy" else "degraded",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "services": {
                "database": db_health,
                "cache": cache_health,
            },
        }

    return app


# ---------------------------------------------------------------------------
# Domain Registration
# ---------------------------------------------------------------------------


def _register_domains(app: FastAPI) -> None:
    """Register all domain routers.

    Each domain exposes a `router` in its package. As domains are migrated
    from the old structure, they get registered here. Until migration is
    complete, the old main.py continues to serve existing routes.
    """
    prefix = "/api/v1"

    # --- Identity ---
    from src.domains.identity.routes import auth_router, users_router

    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(users_router, prefix=f"{prefix}/users", tags=["users"])

    # --- Personal Learning ---
    # from src.domains.personal_learning.routes import router as learning_router
    # app.include_router(learning_router, prefix=f"{prefix}/learning", tags=["personal-learning"])

    # --- Knowledge ---
    # from src.domains.knowledge.routes import router as knowledge_router
    # app.include_router(knowledge_router, prefix=f"{prefix}/knowledge", tags=["knowledge"])

    # --- Learning Spaces ---
    # from src.domains.learning_spaces.routes import router as spaces_router
    # app.include_router(spaces_router, prefix=f"{prefix}/spaces", tags=["learning-spaces"])

    # --- Classrooms ---
    # from src.domains.classrooms.routes import router as classrooms_router
    # app.include_router(classrooms_router, prefix=f"{prefix}/classrooms", tags=["classrooms"])

    # --- Intelligence ---
    # from src.domains.intelligence.routes import router as intelligence_router
    # app.include_router(intelligence_router, prefix=f"{prefix}/intelligence", tags=["intelligence"])

    # --- Progress ---
    # from src.domains.progress.routes import router as progress_router
    # app.include_router(progress_router, prefix=f"{prefix}/progress", tags=["progress"])

    # --- Billing ---
    # from src.domains.billing.routes import router as billing_router
    # app.include_router(billing_router, prefix=f"{prefix}/billing", tags=["billing"])

    # --- Admin ---
    # from src.domains.admin.routes import router as admin_router
    # app.include_router(admin_router, prefix=f"{prefix}/admin", tags=["admin"])

    pass  # Domains will be uncommented as they are migrated


# ---------------------------------------------------------------------------
# Sentry Initialization
# ---------------------------------------------------------------------------


def _init_sentry(settings) -> None:
    """Initialize Sentry error tracking if configured."""
    dsn = settings.SENTRY_DSN
    if not dsn or not dsn.strip():
        logger.warning("Sentry DSN not configured — error tracking disabled")
        return

    placeholders = ["project-id", "your-project-id", "placeholder", "xxx"]
    if any(p in dsn.lower() for p in placeholders):
        logger.warning("Sentry DSN appears to be a placeholder — error tracking disabled")
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            sample_rate=1.0,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            release=settings.APP_VERSION,
        )
        logger.info("Sentry initialized")
    except BadDsn as e:
        logger.warning(f"Invalid Sentry DSN: {e}")
    except Exception as e:
        logger.warning(f"Sentry init failed: {e}")


# ---------------------------------------------------------------------------
# Module-level app instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
