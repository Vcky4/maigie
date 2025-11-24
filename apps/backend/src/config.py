"""Application configuration management."""

import json
from functools import lru_cache
from typing import Annotated, Any, List

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_list_value(value: Any) -> List[str]:
    """Parse list value from various formats."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        # Try JSON array format first
        if value.startswith("[") and value.endswith("]"):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        # Fall back to comma-separated format
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


# Custom type that handles both JSON arrays and comma-separated strings
ListStr = Annotated[List[str], BeforeValidator(parse_list_value)]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Maigie API"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "AI-powered student companion API"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # API
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_HOSTS: ListStr = ["localhost", "127.0.0.1"]

    # CORS
    CORS_ORIGINS: ListStr = [
        "http://localhost:4200",
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: ListStr = ["*"]
    CORS_ALLOW_HEADERS: ListStr = ["*"]

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # OAuth Providers
    OAUTH_GOOGLE_CLIENT_ID: str = ""
    OAUTH_GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_GITHUB_CLIENT_ID: str = ""
    OAUTH_GITHUB_CLIENT_SECRET: str = ""
    OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/oauth/callback"

    # Database (for future use)
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/maigie"

    # Redis Cache
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_KEY_PREFIX: str = "maigie:"
    REDIS_SOCKET_TIMEOUT: int = 5  # seconds
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5  # seconds

    # Celery (Background Workers)
    CELERY_BROKER_URL: str = ""  # Auto-generated from REDIS_URL with DB 1
    CELERY_RESULT_BACKEND: str = ""  # Auto-generated from REDIS_URL with DB 2
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: ListStr = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    CELERY_TASK_ALWAYS_EAGER: bool = False  # Set to True for testing (synchronous execution)
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_TASK_DEFAULT_QUEUE: str = "default"
    CELERY_TASK_DEFAULT_EXCHANGE: str = "tasks"
    CELERY_TASK_DEFAULT_ROUTING_KEY: str = "default"
    CELERY_RESULT_EXPIRES: int = 3600  # seconds (1 hour)

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # WebSocket
    WEBSOCKET_HEARTBEAT_INTERVAL: int = 30  # seconds
    WEBSOCKET_HEARTBEAT_TIMEOUT: int = 60  # seconds
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


def _get_redis_url_with_db(redis_url: str, db_number: int) -> str:
    """Extract base Redis URL and change database number.

    Args:
        redis_url: Full Redis URL (e.g., "redis://localhost:6379/0")
        db_number: Target database number

    Returns:
        Redis URL with updated database number
    """
    # Parse redis://host:port/db or redis://host:port
    if "/" in redis_url:
        base_url = redis_url.rsplit("/", 1)[0]
        return f"{base_url}/{db_number}"
    return f"{redis_url}/{db_number}"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()

    # Auto-generate Celery broker and result backend URLs from REDIS_URL
    # Use separate Redis databases to avoid conflicts:
    # - DB 0: Cache (existing)
    # - DB 1: Celery broker
    # - DB 2: Celery results
    if not settings.CELERY_BROKER_URL:
        settings.CELERY_BROKER_URL = _get_redis_url_with_db(settings.REDIS_URL, 1)
    if not settings.CELERY_RESULT_BACKEND:
        settings.CELERY_RESULT_BACKEND = _get_redis_url_with_db(settings.REDIS_URL, 2)

    return settings

