"""Shared infrastructure adapters (Redis, storage, HTTP clients)."""

from .http import create_http_client
from .redis import Cache, cache, get_cache
from .storage import StorageClient

__all__ = [
    "Cache",
    "cache",
    "get_cache",
    "create_http_client",
    "StorageClient",
]
