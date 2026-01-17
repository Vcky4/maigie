"""
Performance monitoring and optimization utilities.

Provides database query optimization, connection pooling,
and performance metrics tracking.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, Callable, TypeVar

from prometheus_client import Histogram

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Performance metrics
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["query_type", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

API_RESPONSE_DURATION = Histogram(
    "api_response_duration_seconds",
    "API response duration in seconds",
    ["endpoint", "method"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def track_performance(metric: Histogram, labels: dict[str, str] | None = None):
    """
    Decorator to track function performance.

    Args:
        metric: Prometheus histogram metric
        labels: Labels for the metric
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
                raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                if labels:
                    metric.labels(**labels).observe(duration)
                else:
                    metric.observe(duration)
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@asynccontextmanager
async def track_db_query(query_type: str, table: str):
    """
    Context manager to track database query performance.

    Args:
        query_type: Type of query (SELECT, INSERT, UPDATE, DELETE)
        table: Table name
    """
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        DB_QUERY_DURATION.labels(query_type=query_type, table=table).observe(duration)

        # Log slow queries (> 100ms)
        if duration > 0.1:
            logger.warning(
                f"Slow query detected: {query_type} on {table} took {duration:.3f}s",
                extra={"query_type": query_type, "table": table, "duration": duration},
            )


class QueryOptimizer:
    """
    Database query optimization utilities.

    Provides helpers for efficient database queries including:
    - Pagination
    - Field selection
    - Eager loading
    - Query result caching
    """

    @staticmethod
    def paginate_query(
        query,
        page: int = 1,
        page_size: int = 20,
        max_page_size: int = 100,
    ) -> tuple[Any, dict[str, Any]]:
        """
        Apply pagination to a Prisma query.

        Args:
            query: Prisma query builder
            page: Page number (1-indexed)
            page_size: Items per page
            max_page_size: Maximum allowed page size

        Returns:
            Tuple of (paginated query, pagination metadata)
        """
        # Clamp page size
        page_size = min(page_size, max_page_size)
        page = max(1, page)

        # Calculate skip and take
        skip = (page - 1) * page_size
        take = page_size

        # Apply pagination
        paginated_query = query.skip(skip).take(take)

        # Return query and metadata
        metadata = {
            "page": page,
            "page_size": page_size,
            "skip": skip,
            "take": take,
        }

        return paginated_query, metadata

    @staticmethod
    def select_fields(query, fields: list[str] | None = None):
        """
        Select specific fields from a query.

        Args:
            query: Prisma query builder
            fields: List of field names to select

        Returns:
            Query with field selection applied
        """
        if fields:
            # Prisma select syntax
            select_dict = {field: True for field in fields}
            return query.select(select_dict)
        return query

    @staticmethod
    async def get_total_count(query) -> int:
        """
        Get total count for pagination.

        Args:
            query: Prisma query builder (without skip/take)

        Returns:
            Total count
        """
        # Clone query and count
        count_query = query.model.count() if hasattr(query, "model") else query.count()
        return await count_query


def optimize_response_size(data: Any, max_size: int = 1000) -> Any:
    """
    Optimize response size by truncating large arrays.

    Args:
        data: Response data
        max_size: Maximum array size before truncation

    Returns:
        Optimized data
    """
    if isinstance(data, list) and len(data) > max_size:
        logger.warning(f"Response truncated from {len(data)} to {max_size} items")
        return data[:max_size]
    return data
