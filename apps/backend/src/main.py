"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from typing import Annotated

from fastapi import Depends

from .config import get_settings
from .core.cache import cache
from .core.database import db
from .core.websocket import manager as websocket_manager
from .dependencies import SettingsDep
from .utils.dependencies import cleanup_db_client, close_redis_client, get_db_client, get_redis_client, initialize_redis_client
from .exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
)
from .middleware import LoggingMiddleware, SecurityHeadersMiddleware
from .routes.ai import router as ai_router
from .routes.auth import router as auth_router
from .routes.courses import router as courses_router
from .routes.goals import router as goals_router
from .routes.realtime import router as realtime_router
from .routes.resources import router as resources_router
from .routes.schedule import router as schedule_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = get_settings()
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Connect to database (legacy placeholder - kept for compatibility)
    await db.connect()
    print("Legacy database connection initialized")

    # Connect to cache (legacy placeholder - kept for compatibility)
    await cache.connect()
    print("Legacy cache connection initialized")
    
    # Initialize new dependency injection system
    # Prisma client will be initialized on first use via get_db_client()
    
    # Initialize Redis client for dependency injection
    await initialize_redis_client()
    print("Redis client initialized for dependency injection")

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
    
    # Cleanup new dependency injection clients
    await cleanup_db_client()
    await close_redis_client()
    print("Dependency injection clients cleaned up")
    
    # Cleanup legacy connections
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

    return app


# Create app instance
app = create_app()
