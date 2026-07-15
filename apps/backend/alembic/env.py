"""
Alembic environment configuration.

Supports async migrations with asyncpg.
Loads DATABASE_URL from .env automatically.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add project root to path so we can import our models
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env
from dotenv import load_dotenv
load_dotenv()

from src.shared.database.base import Base

# Import all domain models so Alembic can see them for autogenerate
from src.domains.identity.db_models import (  # noqa: F401
    User, UserPreferences, OAuthClient, OAuthCode, OAuthToken,
    DeviceToken, ModelPreference, LimitReachedEmailLog,
)

# Alembic Config object
config = context.config

# Set sqlalchemy.url from environment
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

# Strip pgbouncer param (asyncpg doesn't support it)
if "?pgbouncer=true" in database_url:
    database_url = database_url.replace("?pgbouncer=true", "")
elif "&pgbouncer=true" in database_url:
    database_url = database_url.replace("&pgbouncer=true", "")

config.set_main_option("sqlalchemy.url", database_url)

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
