"""
File storage abstraction.

Currently backed by BunnyCDN. The interface is intentionally
provider-agnostic so we can swap storage backends without
touching domain code.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class StorageClient:
    """Abstract file storage operations.

    Wraps BunnyCDN (or any CDN/S3-compatible provider) behind a
    clean interface that domains can use without knowing the provider.
    """

    def __init__(self, api_key: str | None = None, storage_zone: str | None = None, hostname: str = ""):
        self.api_key = api_key
        self.storage_zone = storage_zone
        self.hostname = hostname

    async def upload(self, path: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload a file and return its public URL.

        Args:
            path: Remote path (e.g., "uploads/user123/avatar.png").
            content: File bytes.
            content_type: MIME type.

        Returns:
            Public URL of the uploaded file.
        """
        # Implementation will be migrated from services/storage_service.py
        raise NotImplementedError("Storage upload not yet migrated")

    async def delete(self, path: str) -> bool:
        """Delete a file from storage.

        Args:
            path: Remote path to delete.

        Returns:
            True if deleted successfully.
        """
        raise NotImplementedError("Storage delete not yet migrated")

    def get_public_url(self, path: str) -> str:
        """Get the public CDN URL for a stored file."""
        return f"https://{self.hostname}/{path}"
