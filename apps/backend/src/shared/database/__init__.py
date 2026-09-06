"""Database lifecycle management — SQLAlchemy async engine."""

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .search import LIKE_ESCAPE, contains_pattern, escape_like, ilike_any
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
    "LIKE_ESCAPE",
    "escape_like",
    "contains_pattern",
    "ilike_any",
    "connect_db",
    "disconnect_db",
    "check_db_health",
    "get_session",
    "get_session_factory",
]
