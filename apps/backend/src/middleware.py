"""
Custom middleware.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings

# Import metrics for Prometheus tracking
try:
    from .utils.metrics import REQUEST_COUNTER, REQUEST_LATENCY
except ImportError:
    # Metrics not available yet (during initial setup)
    REQUEST_COUNTER = None
    REQUEST_LATENCY = None

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request/response logging with structured JSON logging."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details with structured JSON logging."""
        # Start timer
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time
        response_time_ms = duration * 1000  # Convert to milliseconds

        # Extract request details
        method = request.method
        path = request.url.path
        status_code = response.status_code

        # Log structured JSON entry at INFO level
        logger.info(
            "HTTP request processed",
            extra={
                "method": method,
                "path": path,
                "status_code": status_code,
                "response_time_ms": round(response_time_ms, 2),
            },
        )

        # Update Prometheus metrics if available
        if REQUEST_COUNTER is not None and REQUEST_LATENCY is not None:
            REQUEST_COUNTER.labels(method=method, path=path).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(duration)

        # Add custom headers
        response.headers["X-Process-Time"] = str(duration)

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Add security headers to response."""
        response = await call_next(request)

        # Security headers (OWASP recommended)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy (CSP) - adjust based on your needs
        settings = get_settings()
        csp_policy = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://js.stripe.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' https://api.stripe.com https://*.sentry.io; "
            "frame-src https://js.stripe.com; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "upgrade-insecure-requests;"
        )
        response.headers["Content-Security-Policy"] = csp_policy

        # Permissions Policy (formerly Feature Policy)
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )

        # Add HSTS header in production
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response
