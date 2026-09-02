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
from src.shared.middleware import (
    EntitlementScopeMiddleware,
    LoggingMiddleware,
    SecurityHeadersMiddleware,
    UnhandledExceptionMiddleware,
)

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

    # --- Domain event handlers ---
    #
    # `@listen` registers on import, so a handler exists only if something imported its module. Nothing
    # did: before this line, `_handlers` was empty in every process that had merely imported the app,
    # and all ten handlers were unreachable. See `shared/events/registry`.
    from src.shared.events.registry import register_handlers

    logger.info("Domain event handlers registered: %d", register_handlers())

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
        description=(
            "# Maigie API\n\n"
            "The backend for the **Maigie Intelligent Learning Environment**.\n\n"
            "Maigie helps people and organisations continuously develop capability "
            "through personal learning, collaborative Learning Spaces, intelligent "
            "recommendations, and adaptive progress tracking.\n\n"
            "## Domains\n\n"
            "| Domain | Description |\n"
            "|--------|-------------|\n"
            "| **Identity** | Authentication, user profiles, preferences |\n"
            "| **Knowledge** | Courses, modules, topics, resources |\n"
            "| **Personal Learning** | Notes, exam prep, documents, study mode |\n"
            "| **Learning Spaces** | Collaborative environments (membership, seats) |\n"
            "| **Classrooms** | Structured learning within Spaces (sessions, discussions) |\n"
            "| **Intelligence** | AI conversations, memory, recommendations |\n"
            "| **Progress** | Analytics, streaks, spaced repetition, achievements |\n"
            "| **Billing** | Subscriptions, credits, plans, referrals |\n"
            "| **Admin** | Platform administration |\n\n"
            "## Authentication\n\n"
            "All authenticated endpoints require a `Bearer` token in the `Authorization` header.\n"
            "Obtain tokens via `POST /api/v1/auth/login/json` or the OAuth flow.\n"
        ),
        lifespan=lifespan,
        # Swagger UI: always available in dev, hidden in production
        docs_url="/docs" if settings.DEBUG else None,
        # ReDoc: always available (public API reference)
        redoc_url="/redoc",
        openapi_tags=_openapi_tags(),
    )

    # --- Exception Handlers ---
    app.add_exception_handler(MaigieError, maigie_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # --- Middleware (order matters: last added = first executed, so first added is innermost) ---
    #
    # Added first, therefore innermost, therefore it wraps the router more closely than CORS does. That
    # placement is the entire point: Starlette routes the catch-all `Exception` handler registered above
    # through `ServerErrorMiddleware`, which sits *outside* every middleware added here — so the `500` it
    # produces never passes back through `CORSMiddleware` and carries no `Access-Control-Allow-Origin`
    # header. A browser then reports a CORS failure for what is really a server error, pointing at the wrong
    # subsystem entirely, and the client's own error handling never runs because it sees a network failure
    # rather than a status code.
    app.add_middleware(UnhandledExceptionMiddleware)
    # Outside the exception converter and inside everything else, so the memo covers the endpoint and
    # every error path out of it. It has to wrap the router rather than sit beside it, because the
    # scope is what makes `entitlement_service.resolve` answer once per request instead of once per
    # caller; see the module docstring for why websockets are deliberately left out.
    app.add_middleware(EntitlementScopeMiddleware)
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
# OpenAPI Tag Metadata
# ---------------------------------------------------------------------------


def _openapi_tags() -> list[dict]:
    """Tag descriptions shown in ReDoc/Swagger grouped by domain."""
    return [
        {
            "name": "auth",
            "description": (
                "**Identity Domain** — Registration, login, email verification, "
                "password management, OAuth, and token refresh."
            ),
        },
        {
            "name": "users",
            "description": (
                "**Identity Domain** — User profile, preferences, " "account deletion lifecycle."
            ),
        },
        {
            "name": "knowledge",
            "description": (
                "**Knowledge Domain** — Courses, modules, topics, resources, "
                "and the resource bank. Knowledge is reusable and evolves over time."
            ),
        },
        {
            "name": "personal-learning",
            "description": (
                "**Personal Learning Domain** — The learner's private environment: "
                "notes, exam preparation, document generation, study mode."
            ),
        },
        {
            "name": "notifications",
            "description": (
                "**Notifications Domain** — Canonical history, mobile push installations, "
                "delivery evidence, interactions, and best-effort realtime hints."
            ),
        },
        {
            "name": "learning-spaces",
            "description": (
                "**Learning Spaces Domain** — Collaborative learning environments. "
                "Membership, roles (Owner, Admin, Educator, Learner), and space settings."
            ),
        },
        {
            "name": "classrooms",
            "description": (
                "**Classrooms Domain** — Structured learning within a Space: "
                "sessions, discussions, assignments, and assigned courses."
            ),
        },
        {
            "name": "intelligence",
            "description": (
                "**Intelligence Domain** — AI conversations, voice, memory, "
                "recommendations, and the cognitive layer (Observe → Remember → "
                "Reason → Plan → Act)."
            ),
        },
        {
            "name": "progress",
            "description": (
                "**Progress Domain** — Analytics, streaks, achievements, "
                "spaced repetition, goals, and study schedules. "
                "Measures Activity → Progress → Achievement."
            ),
        },
        {
            "name": "billing",
            "description": (
                "**Billing Domain** — Plan catalog, subscriptions (Stripe, Paystack, "
                "Google Play), purchase history, and admin credit adjustments."
            ),
        },
        {
            "name": "webhooks",
            "description": (
                "**Billing Domain** — Payment provider webhook receivers "
                "(Stripe, Paystack, Google Play RTDN). Not called by clients."
            ),
        },
        {
            "name": "admin",
            "description": (
                "**Admin Domain** — Platform administration, content management, "
                "staff operations. Requires staff role."
            ),
        },
        {
            "name": "system",
            "description": "Health checks and infrastructure status.",
        },
    ]


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

    # --- Identity (fully migrated to SQLAlchemy) ---
    from src.domains.identity.oauth_routes import oauth_router
    from src.domains.identity.routes import auth_router, users_router

    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(oauth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(users_router, prefix=f"{prefix}/users", tags=["users"])

    # --- Personal Learning (migrated to SQLAlchemy) ---
    from src.domains.personal_learning.routes import router as learning_router

    app.include_router(learning_router, prefix=f"{prefix}/learning", tags=["personal-learning"])

    # --- Notifications (canonical in-app platform) ---
    from src.domains.notifications.routes import (
        email_webhooks_router,
        push_installations_router,
    )
    from src.domains.notifications.routes import (
        router as notifications_router,
    )

    app.include_router(
        notifications_router, prefix=f"{prefix}/notifications", tags=["notifications"]
    )
    app.include_router(
        push_installations_router,
        prefix=f"{prefix}/push-installations",
        tags=["notifications"],
    )
    # Provider callbacks. Mounted under its own prefix rather than beside the authenticated
    # notification routes, so it is obvious at the routing table which paths are public.
    app.include_router(
        email_webhooks_router,
        prefix=f"{prefix}/webhooks/email",
        tags=["notifications"],
        include_in_schema=False,
    )

    # --- Knowledge (migrated to SQLAlchemy) ---
    from src.domains.knowledge.routes import router as knowledge_router

    app.include_router(knowledge_router, prefix=f"{prefix}/knowledge", tags=["knowledge"])

    # --- Learning Spaces ---
    from src.domains.learning_spaces.routes import router as spaces_router

    app.include_router(spaces_router, prefix=f"{prefix}/spaces", tags=["learning-spaces"])

    # Classrooms will be mounted when their public contract is normalized.
    # from src.domains.classrooms.routes import router as classrooms_router
    # app.include_router(classrooms_router, prefix=f"{prefix}/classrooms", tags=["classrooms"])

    # --- Intelligence (Ask Maigie: conversations, messages, memory, streaming chat) ---
    #
    # Mounted after the schemas it publishes were rewritten, not before. Every response model in
    # `intelligence/models.py` declared camelCase fields on a plain `BaseModel` with
    # `from_attributes=True`, which reads nothing off a snake_case ORM attribute: the conversation
    # endpoints raised on their three required fields and served eight more as declared defaults with a
    # `200`. Mounting first would have exported that shape into `openapi.json` and the generated client
    # types would have been regenerated against it — a client that typechecks cleanly against a lie.
    #
    # Three routes on this prefix were deleted rather than mounted, each because it called a function
    # that does not exist: `POST /chat`, `GET /recommendations`, and the structured reading of
    # `/memory/context`. See the comment blocks in `intelligence/routes.py`.
    from src.domains.intelligence.routes import register_websocket
    from src.domains.intelligence.routes import router as intelligence_router

    app.include_router(intelligence_router, prefix=f"{prefix}/intelligence", tags=["intelligence"])

    # The streaming chat socket at `{prefix}/intelligence/ws`. Registered directly on the app rather
    # than through the router above because it sets its own prefix; see `register_websocket`'s docstring.
    # This is the transport both the web and mobile clients already implement, frame for frame, and it
    # has never been reachable — the function existed solely to be called from here and never was.
    register_websocket(app)

    # --- Progress (migrated to SQLAlchemy) ---
    from src.domains.progress.routes import router as progress_router

    app.include_router(progress_router, prefix=f"{prefix}/progress", tags=["progress"])

    # --- Study Voice ---
    # Mounted at `gemini-live` because two shipped clients hardcode that path, including a released mobile
    # build. The name puts a vendor in a public URL and should become `/study/voice`; that is a coordinated
    # rename across three repositories, not something to do while restoring the feature.
    from src.domains.study_voice import router as study_voice_router

    app.include_router(study_voice_router, prefix=f"{prefix}/gemini-live", tags=["study-voice"])

    # --- Billing ---
    #
    # Mounted. Until now it was not, and the consequence was the most expensive thing in the
    # codebase: `credit_consumption_service` is imported directly by `study_voice`,
    # `personal_learning` and `knowledge`, so the meter has been running the whole time —
    # while every checkout, verification and webhook endpoint that could have paid it was
    # served by nothing. Both clients call these paths today and get a 404. A learner who
    # exhausted their allowance had no reachable way to buy more, and no trial has ever
    # converted to a paying subscriber because there has never been a checkout to convert
    # into.
    #
    # Mounted only after the catalogue it serves was rewritten, not before. Serving the old
    # one would have exported credit packs, yearly Plus and the rewarded-ad endpoints into
    # `openapi.json`, and the generated client types would have been regenerated against
    # products that are being withdrawn in the same change.
    #
    # Three endpoints are absent by choice rather than oversight: `/referrals/*`, which
    # would answer 500 from a Prisma sentinel, and the credit-pack product verification.
    # See the comment blocks in `billing/routes.py`.
    from src.domains.billing.routes import router as billing_router
    from src.domains.billing.webhooks import router as webhooks_router

    app.include_router(billing_router, prefix=f"{prefix}/billing", tags=["billing"])
    # Provider callbacks. Its own prefix, so the routing table shows at a glance which
    # paths are unauthenticated and signature-verified rather than user-authenticated.
    app.include_router(webhooks_router, prefix=f"{prefix}/webhooks", tags=["webhooks"])

    # --- Admin (pending SQLAlchemy migration) ---
    # from src.domains.admin.routes import router as admin_router
    # app.include_router(admin_router, prefix=f"{prefix}/admin", tags=["admin"])


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
