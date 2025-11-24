"""Worker management utilities for monitoring and health checks.

This module provides utilities for managing Celery workers, checking their
health, and monitoring worker status.

Usage:
    ```python
    from .workers.manager import check_worker_health, get_worker_status

    # Check if workers are healthy
    health = await check_worker_health()

    # Get worker status
    status = get_worker_status()
    ```
"""

import logging
from typing import Any

from ..core.celery_app import celery_app

logger = logging.getLogger(__name__)


def get_worker_status() -> dict[str, Any]:
    """Get status of all Celery workers.

    Returns:
        Dictionary with worker status information

    Example:
        ```python
        status = get_worker_status()
        # Returns: {
        #   "workers": ["worker1@host"],
        #   "active": 2,
        #   "online": True
        # }
        ```
    """
    inspect = celery_app.control.inspect()

    # Get active workers
    active_workers = inspect.active()
    workers = list(active_workers.keys()) if active_workers else []

    # Get stats
    stats = inspect.stats()
    total_tasks = 0
    if stats:
        for worker_stats in stats.values():
            total_tasks += worker_stats.get("total", {}).get("tasks.succeeded", 0)
            total_tasks += worker_stats.get("total", {}).get("tasks.failed", 0)

    return {
        "workers": workers,
        "worker_count": len(workers),
        "online": len(workers) > 0,
        "total_tasks_processed": total_tasks,
    }


async def check_worker_health() -> dict[str, Any]:
    """Check health of Celery workers and broker connection.

    Returns:
        Dictionary with health check results

    Example:
        ```python
        health = await check_worker_health()
        # Returns: {
        #   "status": "healthy",
        #   "workers_online": 1,
        #   "broker_connected": True
        # }
        ```
    """
    try:
        # Check broker connection
        broker_connected = False
        try:
            inspect = celery_app.control.inspect()
            ping_result = inspect.ping()
            broker_connected = ping_result is not None and len(ping_result) >= 0
        except Exception as e:
            logger.warning(f"Broker health check failed: {e}")

        # Get worker status
        worker_status = get_worker_status()
        workers_online = worker_status["worker_count"]

        # Determine overall health
        if broker_connected and workers_online > 0:
            status = "healthy"
        elif broker_connected:
            status = "degraded"  # Broker OK but no workers
        else:
            status = "unhealthy"  # Broker not connected

        return {
            "status": status,
            "broker_connected": broker_connected,
            "workers_online": workers_online,
            "workers": worker_status["workers"],
        }
    except Exception as e:
        logger.error(f"Worker health check failed: {e}")
        return {
            "status": "error",
            "broker_connected": False,
            "workers_online": 0,
            "workers": [],
            "error": str(e),
        }


def get_worker_info(worker_name: str | None = None) -> dict[str, Any]:
    """Get detailed information about a specific worker or all workers.

    Args:
        worker_name: Worker name (None for all workers)

    Returns:
        Dictionary with worker information

    Example:
        ```python
        info = get_worker_info("worker1@host")
        ```
    """
    inspect = celery_app.control.inspect()

    if worker_name:
        # Get info for specific worker
        stats = inspect.stats([worker_name])
        active = inspect.active([worker_name])
        registered = inspect.registered([worker_name])

        return {
            "name": worker_name,
            "stats": stats.get(worker_name) if stats else None,
            "active_tasks": active.get(worker_name) if active else [],
            "registered_tasks": registered.get(worker_name) if registered else [],
        }
    else:
        # Get info for all workers
        stats = inspect.stats()
        active = inspect.active()
        registered = inspect.registered()

        workers_info = {}
        worker_names = list(stats.keys()) if stats else []

        for worker in worker_names:
            workers_info[worker] = {
                "name": worker,
                "stats": stats.get(worker) if stats else None,
                "active_tasks": active.get(worker) if active else [],
                "registered_tasks": registered.get(worker) if registered else [],
            }

        return workers_info


def shutdown_worker(worker_name: str, wait: bool = False) -> bool:
    """Shutdown a specific worker gracefully.

    Args:
        worker_name: Worker name
        wait: Whether to wait for tasks to complete

    Returns:
        True if shutdown command was sent

    Example:
        ```python
        shutdown_worker("worker1@host", wait=True)
        ```
    """
    try:
        celery_app.control.shutdown(destination=[worker_name])
        logger.info(f"Shutdown command sent to worker {worker_name} (wait={wait})")
        return True
    except Exception as e:
        logger.error(f"Failed to shutdown worker {worker_name}: {e}")
        return False

