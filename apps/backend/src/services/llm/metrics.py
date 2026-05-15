"""Prometheus metrics for multi-provider LLM routing.

Tracks routing decisions, fallback events, circuit breaker state changes,
and per-provider request latency/cost.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from prometheus_client import Counter, Histogram, Gauge, REGISTRY

# ---------------------------------------------------------------------------
# Routing decision metrics
# ---------------------------------------------------------------------------

LLM_REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "Total LLM requests routed, labeled by provider, model, task, and outcome",
    ["provider", "model", "task", "outcome"],
    registry=REGISTRY,
)

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "LLM request duration in seconds (including tool loops)",
    ["provider", "model", "task"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Fallback and retry metrics
# ---------------------------------------------------------------------------

LLM_FALLBACK_TOTAL = Counter(
    "llm_fallback_total",
    "Number of times a fallback provider was used after primary failure",
    ["from_provider", "from_model", "to_provider", "to_model", "reason"],
    registry=REGISTRY,
)

LLM_ALL_PROVIDERS_EXHAUSTED = Counter(
    "llm_all_providers_exhausted_total",
    "Number of times all provider-model candidates were exhausted",
    ["task"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Circuit breaker metrics
# ---------------------------------------------------------------------------

LLM_CIRCUIT_BREAKER_STATE = Gauge(
    "llm_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["provider", "model"],
    registry=REGISTRY,
)

LLM_CIRCUIT_BREAKER_TRIPS = Counter(
    "llm_circuit_breaker_trips_total",
    "Number of times a circuit breaker tripped from CLOSED to OPEN",
    ["provider", "model"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Cost metrics
# ---------------------------------------------------------------------------

LLM_COST_USD = Counter(
    "llm_cost_usd_total",
    "Total LLM cost in USD, labeled by provider, model, and user tier",
    ["provider", "model", "user_tier"],
    registry=REGISTRY,
)

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total tokens consumed, labeled by provider, model, and direction",
    ["provider", "model", "direction"],
    registry=REGISTRY,
)
