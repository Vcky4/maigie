"""
Application configuration management.

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
ListStr = Annotated[list[str], BeforeValidator(parse_list_value)]


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

    # Database 
    DATABASE_URL: str = ""

    # Redis Cache
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_KEY_PREFIX: str = "maigie:"
    REDIS_SOCKET_TIMEOUT: int = 5  # seconds
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5  # seconds

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Error Tracking (Sentry)
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # WebSocket
    WEBSOCKET_HEARTBEAT_INTERVAL: int = 30  # seconds
    WEBSOCKET_HEARTBEAT_TIMEOUT: int = 60  # seconds
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 5

    model_config = SettingsConfigDict(
        # Look for .env file in the backend directory
        # __file__ is apps/backend/src/config.py
        # parent = apps/backend/src/
        # parent.parent = apps/backend/
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
