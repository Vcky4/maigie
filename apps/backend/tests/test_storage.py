"""Tests for the BunnyCDN storage client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from src.shared.infrastructure.storage import (
    BunnyStorageClient,
    StorageError,
    storage_service,
)


def _mock_settings(
    api_key: str | None = "test-key",
    zone: str | None = "test-zone",
    hostname: str = "cdn.test.com",
    public_base: str | None = None,
    region: str = "uk",
) -> MagicMock:
    s = MagicMock()
    s.BUNNY_CDN_API_KEY = api_key
    s.BUNNY_STORAGE_ZONE = zone
    s.BUNNY_CDN_HOSTNAME = hostname
    s.BUNNY_PUBLIC_URL_BASE = public_base
    s.BUNNY_STORAGE_REGION = region
    return s


def _fresh_client(settings: MagicMock) -> BunnyStorageClient:
    """Build a fresh client with patched settings."""
    with patch("src.shared.infrastructure.storage.get_settings", return_value=settings):
        client = BunnyStorageClient()
        client._ensure_init()
    return client


@pytest.mark.asyncio
async def test_upload_bytes_success():
    client = _fresh_client(_mock_settings())

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.put.return_value = MagicMock(status_code=201)
        mock_client_cls.return_value.__aenter__.return_value = http

        result = await client.upload_bytes(b"hello", "notes/hello.txt")

    assert result["filename"] == "hello.txt"
    assert result["url"] == "https://cdn.test.com/notes/hello.txt"
    assert result["size"] == 5
    assert http.put.call_args[0][0] == (
        "https://uk.storage.bunnycdn.com/test-zone/notes/hello.txt"
    )


@pytest.mark.asyncio
async def test_upload_bytes_uses_public_base_when_set():
    client = _fresh_client(_mock_settings(public_base="https://pull.example.net"))

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.put.return_value = MagicMock(status_code=201)
        mock_client_cls.return_value.__aenter__.return_value = http

        result = await client.upload_bytes(b"x", "chat-images/a.png")

    assert result["url"] == "https://pull.example.net/chat-images/a.png"


@pytest.mark.asyncio
async def test_upload_bytes_missing_config_raises():
    client = _fresh_client(_mock_settings(api_key=None))

    with pytest.raises(StorageError, match="Storage configuration missing"):
        await client.upload_bytes(b"x", "a.txt")


@pytest.mark.asyncio
async def test_upload_bytes_api_error_raises():
    client = _fresh_client(_mock_settings())

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.put.return_value = MagicMock(status_code=401, text="Unauthorized")
        mock_client_cls.return_value.__aenter__.return_value = http

        with pytest.raises(StorageError, match="HTTP 401"):
            await client.upload_bytes(b"x", "a.txt")


@pytest.mark.asyncio
async def test_upload_upload_file_delegates_to_upload_bytes():
    client = _fresh_client(_mock_settings())

    file = MagicMock(spec=UploadFile)
    file.filename = "test image.png"
    file.content_type = "image/png"
    file.read = AsyncMock(return_value=b"pngbytes")
    file.seek = AsyncMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.put.return_value = MagicMock(status_code=201)
        mock_client_cls.return_value.__aenter__.return_value = http

        result = await client.upload_upload_file(file, path_prefix="uploads")

    assert result["filename"] == "test_image.png"
    assert result["url"] == "https://cdn.test.com/uploads/test_image.png"
    file.seek.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_delete_by_public_url():
    client = _fresh_client(_mock_settings())

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.delete.return_value = MagicMock(status_code=200)
        mock_client_cls.return_value.__aenter__.return_value = http

        ok = await client.delete("https://cdn.test.com/notes/hello.txt")

    assert ok is True
    assert http.delete.call_args[0][0] == (
        "https://uk.storage.bunnycdn.com/test-zone/notes/hello.txt"
    )


@pytest.mark.asyncio
async def test_delete_returns_true_on_404():
    client = _fresh_client(_mock_settings())

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.delete.return_value = MagicMock(status_code=404)
        mock_client_cls.return_value.__aenter__.return_value = http

        assert await client.delete("notes/missing.txt") is True


@pytest.mark.asyncio
async def test_fetch_bytes_returns_content_and_type():
    client = _fresh_client(_mock_settings())

    with patch("httpx.AsyncClient") as mock_client_cls:
        http = AsyncMock()
        http.get.return_value = MagicMock(
            status_code=200,
            content=b"png-bytes",
            headers={"content-type": "image/png"},
        )
        mock_client_cls.return_value.__aenter__.return_value = http

        result = await client.fetch_bytes("chat-images/a.png")

    assert result == (b"png-bytes", "image/png")


def test_singleton_instance_exists():
    """The module exposes a shared instance."""
    assert isinstance(storage_service, BunnyStorageClient)
