"""
Admin System Health Monitor.

Provides a comprehensive view of all system services, their status,
and per-model LLM health (circuit breaker states).
"""

import logging
import os
import time
from datetime import UTC, datetime

from fastapi import APIRouter

from ..dependencies import AdminUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/system-health", tags=["admin-health"])


@router.get("")
async def get_system_health(admin_user: AdminUser):
    """Get health status of all system services and LLM models."""
    results: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "services": {},
        "llm_models": {},
        "overall": "healthy",
    }

    # 1. API Server
    try:
        import resource

        mem_usage_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        mem_usage_mb = None

    results["services"]["api"] = {
        "status": "healthy",
        "memory_mb": mem_usage_mb,
        "pid": os.getpid(),
    }

    # 2. Database (Prisma)
    try:
        from ..core.database import db

        start = time.time()
        await db.query_raw("SELECT 1")
        latency_ms = round((time.time() - start) * 1000, 1)
        results["services"]["database"] = {
            "status": "healthy",
            "latency_ms": latency_ms,
        }
    except Exception as e:
        results["services"]["database"] = {
            "status": "unhealthy",
            "error": str(e)[:100],
        }
        results["overall"] = "degraded"

    # 3. Redis
    try:
        from ..core.cache import cache

        health = await cache.health_check()
        results["services"]["redis"] = {
            "status": "healthy" if health.get("connected") else "unhealthy",
            "latency_ms": health.get("latency_ms"),
            "memory_mb": health.get("memory_mb"),
            "connected_clients": health.get("connected_clients"),
        }
        if not health.get("connected"):
            results["overall"] = "degraded"
    except Exception as e:
        results["services"]["redis"] = {
            "status": "unhealthy",
            "error": str(e)[:100],
        }
        results["overall"] = "degraded"

    # 4. Celery Worker
    try:
        from ..core.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=3.0)
        ping_result = inspect.ping()

        if ping_result and len(ping_result) > 0:
            worker_names = list(ping_result.keys())
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}

            total_active = sum(len(tasks) for tasks in active.values())
            total_reserved = sum(len(tasks) for tasks in reserved.values())

            results["services"]["celery_worker"] = {
                "status": "healthy",
                "workers_online": len(worker_names),
                "worker_names": worker_names,
                "active_tasks": total_active,
                "queued_tasks": total_reserved,
            }
        else:
            results["services"]["celery_worker"] = {
                "status": "unhealthy",
                "error": "No workers responding to ping",
                "workers_online": 0,
            }
            results["overall"] = "degraded"
    except Exception as e:
        results["services"]["celery_worker"] = {
            "status": "unknown",
            "error": str(e)[:100],
        }

    # 5. Celery Beat (infer from last scheduled task)
    try:
        from ..core.celery_app import celery_app

        beat_schedule = celery_app.conf.beat_schedule or {}
        results["services"]["celery_beat"] = {
            "status": "healthy" if len(beat_schedule) > 0 else "unknown",
            "scheduled_tasks": len(beat_schedule),
            "task_names": sorted(beat_schedule.keys())[:15],
        }
    except Exception as e:
        results["services"]["celery_beat"] = {
            "status": "unknown",
            "error": str(e)[:100],
        }

    # 6. WebSocket connections
    try:
        from ..services.socket_manager import manager

        results["services"]["websocket"] = {
            "status": "healthy",
            "active_connections": (
                len(manager.active_connections) if hasattr(manager, "active_connections") else 0
            ),
        }
    except Exception:
        results["services"]["websocket"] = {"status": "unknown"}

    # 7. LLM Models — Circuit breaker state per model
    try:
        from ..services.llm.adapter_registry import get_llm_router

        llm_router = get_llm_router()
        cb = llm_router._circuit_breaker

        # Get all known model keys from the adapter registry
        adapter_keys = list(llm_router._adapter_registry.keys())

        for adapter_key in adapter_keys:
            parts = adapter_key.split(":", 1)
            if len(parts) != 2:
                continue
            provider, model = parts

            key = cb._key(provider, model)
            raw_state = cb._get_raw_state(key)
            failures = cb._failures_in_window(key)
            last_failure = cb._last_failure_time.get(key)

            # Determine health status
            if raw_state.value == "open":
                status = "unhealthy"
                results["overall"] = "degraded"
            elif raw_state.value == "half_open":
                status = "degraded"
            elif failures > 0:
                status = "warning"
            else:
                status = "healthy"

            results["llm_models"][adapter_key] = {
                "provider": provider,
                "model": model,
                "circuit_state": raw_state.value,
                "failures_in_window": failures,
                "failure_threshold": cb._failure_threshold,
                "last_failure": (
                    datetime.fromtimestamp(last_failure, tz=UTC).isoformat()
                    if last_failure
                    else None
                ),
                "status": status,
            }
    except Exception as e:
        results["llm_models"] = {"error": str(e)[:200]}

    # Compute overall status
    service_statuses = [s.get("status") for s in results["services"].values()]
    if "unhealthy" in service_statuses:
        results["overall"] = "unhealthy"
    elif "degraded" in service_statuses or results["overall"] == "degraded":
        results["overall"] = "degraded"

    return results
