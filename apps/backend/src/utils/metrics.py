"""
Prometheus metrics for API monitoring and business metrics tracking.

Copyright (C) 2025 Maigie

Licensed under the Apache License, Version 2.0.
See LICENSE-APACHE-2.0.md file in the repository root for details.
"""

from prometheus_client import REGISTRY, Counter, Histogram

# Request metrics - labeled by method and path
REQUEST_COUNTER = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "path"],
    registry=REGISTRY,
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    registry=REGISTRY,
)

# Business logic metric - AI usage tracking for quota enforcement
AI_USAGE_COUNTER = Counter(
    "ai_usage_total",
    "Total number of AI processing requests (for subscription quota enforcement)",
    registry=REGISTRY,
)
