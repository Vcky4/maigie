"""
File storage — BunnyCDN backend.

Provider-agnostic interface so domains never talk to BunnyCDN directly.
Swap out ``BunnyStorageClient`` for another provider (S3, R2, GCS) without
touching domain code.

Config comes from ``src.config`` (``BUNNY_CDN_API_KEY``, ``BUNNY_STORAGE_ZONE``,
``BUNNY_CDN_HOSTNAME``, ``BUNNY_PUBLIC_URL_BASE``).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when a storage operation fails."""


class BunnyStorageClient:
    """
    BunnyCDN Storage API client.

    All methods raise ``StorageError`` on failure. The client is lazy —
    it reads settings the first time a method is called so tests can
    patch settings without recreating the singleton.
    """

    # Default region host for BunnyCDN storage.
    # Override via config if you use a different region (ny, la, sg, se, syd, br, jh).
    _REGION_HOSTS = {
        "de": "storage.bunnycdn.com",  # Falkenstein, Germany
        "uk": "uk.storage.bunnycdn.com",  # default — London
        "ny": "ny.storage.bunnycdn.com",
        "la": "la.storage.bunnycdn.com",
        "sg": "sg.storage.bunnycdn.com",
        "se": "se.storage.bunnycdn.com",
        "syd": "syd.storage.bunnycdn.com",
        "br": "br.storage.bunnycdn.com",
        "jh": "jh.storage.bunnycdn.com",
    }

    def __init__(self):
        self._initialized = False
        self.api_key: str | None = None
        self.storage_zone: str | None = None
        self.cdn_hostname: str = ""
        self.public_url_base: str | None = None
        self.base_url: str = ""

    # ------------------------------------------------------------------ setup

    def _ensure_init(self) -> None:
        """Load config lazily so patched settings in tests are respected."""
        if self._initialized:
            return
        settings = get_settings()
        self.api_key = getattr(settings, "BUNNY_CDN_API_KEY", None)
        self.storage_zone = getattr(settings, "BUNNY_STORAGE_ZONE", None)
        self.cdn_hostname = getattr(settings, "BUNNY_CDN_HOSTNAME", "") or ""
        raw_base = (getattr(settings, "BUNNY_PUBLIC_URL_BASE", None) or "").strip().rstrip("/")
        self.public_url_base = raw_base or None

        region = (getattr(settings, "BUNNY_STORAGE_REGION", "uk") or "uk").lower()
        host = self._REGION_HOSTS.get(region, self._REGION_HOSTS["uk"])
        if self.storage_zone:
            self.base_url = f"https://{host}/{self.storage_zone}"
        self._initialized = True

    def _require_config(self) -> None:
        self._ensure_init()
        if not self.api_key or not self.storage_zone:
            raise StorageError(
                "Storage configuration missing — set BUNNY_CDN_API_KEY and BUNNY_STORAGE_ZONE"
            )

    def _public_url(self, remote_path: str) -> str:
        """Build the public URL for a stored path."""
        path = remote_path.lstrip("/")
        if self.public_url_base:
            return f"{self.public_url_base}/{path}"
        return f"https://{self.cdn_hostname}/{path}"

    # ------------------------------------------------------------------ upload

    async def upload_bytes(
        self,
        content: bytes,
        remote_path: str,
        *,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """
        Upload raw bytes to storage.

        Args:
            content: File bytes.
            remote_path: Destination path within the storage zone
                (e.g., ``"generated-docs/user123/report.pdf"``).
            content_type: MIME type. Kept for API symmetry — BunnyCDN
                does not store it, but downstream consumers may need it.

        Returns:
            ``{"filename": str, "url": str, "size": int}``.
        """
        self._require_config()
        path = remote_path.lstrip("/")
        upload_url = f"{self.base_url}/{path}"

        headers = {
            "AccessKey": self.api_key or "",
            "Content-Type": content_type,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.put(upload_url, headers=headers, content=content)
        except httpx.HTTPError as e:
            logger.error(f"BunnyCDN upload transport error for {path}: {e}")
            raise StorageError(f"Storage upload failed: {e}") from e

        if response.status_code not in (200, 201):
            logger.error(
                f"BunnyCDN upload failed for {path}: {response.status_code} {response.text}"
            )
            raise StorageError(f"Storage upload failed: HTTP {response.status_code}")

        return {
            "filename": path.rsplit("/", 1)[-1],
            "url": self._public_url(path),
            "size": len(content),
            "path": path,
        }

    async def upload_upload_file(self, file: Any, path_prefix: str = "") -> dict[str, Any]:
        """
        Upload a FastAPI ``UploadFile`` to storage.

        Args:
            file: A ``fastapi.UploadFile`` instance.
            path_prefix: Optional folder prefix (e.g., ``"notes/user123"``).

        Returns:
            ``{"filename": str, "url": str, "size": int}``.
        """
        filename = (file.filename or "upload.bin").replace(" ", "_")
        remote_path = f"{path_prefix.strip('/')}/{filename}" if path_prefix else filename
        content = await file.read()
        try:
            content_type = getattr(file, "content_type", None) or "application/octet-stream"
            return await self.upload_bytes(content, remote_path, content_type=content_type)
        finally:
            # Reset pointer in case the caller reads the file again.
            try:
                await file.seek(0)
            except Exception:
                pass

    # ------------------------------------------------------------------ delete

    async def delete(self, url_or_path: str) -> bool:
        """
        Delete a file by public URL or storage path.

        Returns ``True`` if the file is gone from storage (deleted or
        did not exist), ``False`` on transport/auth errors.
        """
        self._ensure_init()
        if not self.api_key or not self.storage_zone:
            return False

        path = self._normalize_path(url_or_path)
        if not path:
            return False

        delete_url = f"{self.base_url}/{path}"
        headers = {"AccessKey": self.api_key}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(delete_url, headers=headers)
            # 200 = deleted, 404 = already gone (idempotent)
            return response.status_code in (200, 404)
        except httpx.HTTPError as e:
            logger.warning(f"Storage delete failed for {path}: {e}")
            return False

    # ------------------------------------------------------------------ fetch

    async def fetch_bytes(self, url_or_path: str) -> tuple[bytes, str] | None:
        """
        Download an object from storage. Useful when the public CDN
        edge is misbehaving and we need to bypass it.

        Returns ``(content, content_type)`` or ``None`` on error.
        """
        self._ensure_init()
        if not self.api_key or not self.storage_zone:
            return None

        path = self._normalize_path(url_or_path)
        if not path or ".." in path:
            return None

        get_url = f"{self.base_url}/{path}"
        headers = {"AccessKey": self.api_key}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(get_url, headers=headers)
            if response.status_code != 200:
                return None
            ct = response.headers.get("content-type", "application/octet-stream")
            return response.content, ct
        except httpx.HTTPError as e:
            logger.warning(f"Storage fetch failed for {path}: {e}")
            return None

    # ---------------------------------------------------------------- helpers

    def _normalize_path(self, url_or_path: str) -> str:
        """Accept either a public URL or a storage-relative path and return the path."""
        if not url_or_path:
            return ""
        if "://" in url_or_path:
            parsed = urlparse(url_or_path)
            return (parsed.path or "").lstrip("/")
        return url_or_path.lstrip("/")

    def chat_images_storage_path(self, public_url: str) -> str | None:
        """Return the storage path for a chat-image URL, if it lives in our storage."""
        path = self._normalize_path(public_url)
        if path.startswith("chat-images/"):
            return path
        return None

    async def fetch_public_chat_image_bytes(self, public_url: str) -> tuple[bytes, str] | None:
        """Backwards-compatible helper used by chat/vision flows."""
        path = self.chat_images_storage_path(public_url)
        if not path:
            return None
        return await self.fetch_bytes(path)


# Module-level singleton used across the app.
storage_service = BunnyStorageClient()
