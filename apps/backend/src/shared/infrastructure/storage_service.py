"""
Backwards-compatible import path for the storage service singleton.

Prefer::

    from src.shared.infrastructure.storage import storage_service
"""

from src.shared.infrastructure.storage import (
    BunnyStorageClient,
    StorageError,
    storage_service,
)

__all__ = ["BunnyStorageClient", "StorageError", "storage_service"]
