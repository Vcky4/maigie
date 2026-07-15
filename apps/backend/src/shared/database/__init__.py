"""Database client lifecycle management."""

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .client import db
from .session import (
    check_db_health,
    connect_db,
    disconnect_db,
    get_session,
    get_session_factory,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "db",
    "connect_db",
    "disconnect_db",
    "check_db_health",
    "get_session",
    "get_session_factory",
]
