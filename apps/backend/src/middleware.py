"""DEPRECATED — Re-exports from src.shared.middleware for backward compatibility."""

from src.shared.middleware import LoggingMiddleware, SecurityHeadersMiddleware

__all__ = ["LoggingMiddleware", "SecurityHeadersMiddleware"]
