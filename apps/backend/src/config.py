"""
Application configuration management.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_list_value(value: Any) -> list[str]:
    """Parse list value from various formats."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("[") and value.endswith("]"):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


ListStr = Annotated[list[str], BeforeValidator(parse_list_value)]


class Settings(BaseSettings):
    # ... existing settings ...

    # --- Email ---
    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: str | None = None
    EMAILS_FROM_NAME: str | None = None

    # --- Application Info ---
    APP_NAME: str = "Maigie API"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "AI-powered student companion API"  # <--- THIS WAS MISSING
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # --- API & URLs ---
    API_V1_STR: str = "/api/v1"  # Renamed from API_V1_PREFIX to match auth.py
    ALLOWED_HOSTS: ListStr = ["localhost", "127.0.0.1"]
    FRONTEND_BASE_URL: str = ""  # For OAuth redirects

    # --- CORS ---
    CORS_ORIGINS: ListStr = [
        "http://localhost:4200",
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: ListStr = ["*"]
    CORS_ALLOW_HEADERS: ListStr = ["*"]

    # --- Security & Auth ---
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Database ---
    DATABASE_URL: str = ""  # Loaded from .env

    # --- Redis Cache ---
    REDIS_URL: str = "redis://localhost:6379/0"

    REDIS_KEY_PREFIX: str = "maigie:"
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5

    # --- WebSocket ---
    WEBSOCKET_HEARTBEAT_INTERVAL: int = 30
    WEBSOCKET_HEARTBEAT_TIMEOUT: int = 60
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 5

    # --- OAuth Providers (Placeholders) ---
    OAUTH_GOOGLE_CLIENT_ID: str | None = None
    OAUTH_GOOGLE_CLIENT_SECRET: str | None = None
    # TODO: Enable GitHub OAuth provider in the future
    # OAUTH_GITHUB_CLIENT_ID: str | None = None
    # OAUTH_GITHUB_CLIENT_SECRET: str | None = None
    # Note: OAUTH_REDIRECT_URI is not used - redirect URI is dynamically constructed
    # from request.base_url in the OAuth routes. This setting is kept for reference only.
    OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/oauth/callback"

    # --- Celery (Background Workers) ---
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: ListStr = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    CELERY_TASK_ALWAYS_EAGER: bool = False
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_TASK_DEFAULT_QUEUE: str = "default"
    CELERY_TASK_DEFAULT_EXCHANGE: str = "tasks"
    CELERY_TASK_DEFAULT_ROUTING_KEY: str = "default"
    CELERY_RESULT_EXPIRES: int = 3600

    # --- Logging & Sentry ---
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


def _get_redis_url_with_db(redis_url: str, db_number: int) -> str:
    if "/" in redis_url:
        base_url = redis_url.rsplit("/", 1)[0]
        return f"{base_url}/{db_number}"
    return f"{redis_url}/{db_number}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    # Auto-generate Celery URLs
    if not settings.CELERY_BROKER_URL:
        settings.CELERY_BROKER_URL = _get_redis_url_with_db(settings.REDIS_URL, 1)
    if not settings.CELERY_RESULT_BACKEND:
        settings.CELERY_RESULT_BACKEND = _get_redis_url_with_db(settings.REDIS_URL, 2)

    return settings


# Create the instance
settings = get_settings()
