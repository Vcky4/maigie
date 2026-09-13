"""System health composite for the admin monitor.

Assembles a single honest snapshot the admin System Health page reads directly:
``{overall, services, llm_models, version, environment}``. Every service card traces to a real
probe (database, cache, Celery workers) — nothing is invented. Services we cannot honestly probe
from this process (a dedicated WebSocket gateway, a separate Celery beat process, per-service memory)
are simply omitted rather than shown as a fabricated "healthy" card.

``llm_models`` reports the circuit-breaker state observed *in this process*. It is a resilience
signal ("has this process's router tripped a provider?"), not a liveness ping — an empty map is the
healthy normal case, and if no LLM routing has happened here yet there is nothing to report.
"""

from __future__ import annotations

import time
from typing import Any

# Statuses the client renders with a distinct colour. Anything else collapses to "unknown".
_HEALTHY = "healthy"
_DEGRADED = "degraded"
_UNHEALTHY = "unhealthy"


def _normalise_status(raw: str | None) -> str:
    """Map the varied helper statuses onto the four the client colours."""
    if raw == "healthy":
        return _HEALTHY
    if raw in ("degraded", "warning"):
        return _DEGRADED
    if raw in ("unhealthy", "disconnected", "unavailable", "error"):
        return _UNHEALTHY
    return "unknown"


async def _database_card() -> dict[str, Any]:
    from src.shared.database import check_db_health

    started = time.perf_counter()
    health = await check_db_health()
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    card: dict[str, Any] = {
        "status": _normalise_status(health.get("status")),
        "latency_ms": latency_ms,
    }
    if health.get("error"):
        card["error"] = health["error"]
    return card


async def _redis_card() -> dict[str, Any]:
    from src.core.cache import cache

    started = time.perf_counter()
    health = await cache.health_check()
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    card: dict[str, Any] = {
        "status": _normalise_status(health.get("status")),
        "latency_ms": latency_ms,
    }
    if health.get("error"):
        card["error"] = health["error"]
    return card


async def _worker_card() -> dict[str, Any]:
    try:
        from src.workers.manager import check_worker_health

        health = await check_worker_health()
    except Exception as exc:  # pragma: no cover - defensive: worker stack optional
        return {"status": _UNHEALTHY, "workers_online": 0, "error": str(exc)}

    card: dict[str, Any] = {
        "status": _normalise_status(health.get("status")),
        "workers_online": health.get("workers_online", 0),
    }
    if health.get("error"):
        card["error"] = health["error"]
    return card


def _llm_models() -> dict[str, Any]:
    """Observed circuit-breaker state for this process, or empty if no router is live here."""
    from src.domains.intelligence.reasoning.llm import adapter_registry

    # Read the module-level singleton directly rather than get_llm_router(): a health check must not
    # force-build the router (and its adapters) as a side effect. No router yet => nothing observed.
    router = adapter_registry._llm_router_instance
    if router is None:
        return {}
    try:
        return router._circuit_breaker.snapshot()
    except Exception:  # pragma: no cover - defensive
        return {}


def _overall(services: dict[str, dict], llm_models: dict[str, dict]) -> str:
    """Worst-of the real service statuses; an open LLM breaker degrades but never marks unhealthy."""
    statuses = [card.get("status", "unknown") for card in services.values()]
    if _UNHEALTHY in statuses:
        return _UNHEALTHY
    if _DEGRADED in statuses:
        return _DEGRADED
    if any(model.get("status") in (_DEGRADED, _UNHEALTHY) for model in llm_models.values()):
        return _DEGRADED
    if statuses and all(status == _HEALTHY for status in statuses):
        return _HEALTHY
    return "unknown"


async def snapshot() -> dict[str, Any]:
    from src.config import get_settings

    settings = get_settings()

    services: dict[str, Any] = {
        # The API is answering this very request, so it is healthy by construction.
        "api": {"status": _HEALTHY},
        "database": await _database_card(),
        "redis": await _redis_card(),
        "celery_worker": await _worker_card(),
    }
    llm_models = _llm_models()

    return {
        "overall": _overall(services, llm_models),
        "services": services,
        "llm_models": llm_models,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }
