import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import UploadFile, HTTPException
from src.services.storage_service import StorageService

@pytest.mark.asyncio
async def test_upload_file_success():
    """Test successful file upload to BunnyCDN."""
    # Mock settings
    with patch("src.services.storage_service.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.BUNNY_CDN_API_KEY = "test-key"
        mock_settings.BUNNY_STORAGE_ZONE = "test-zone"
        mock_settings.BUNNY_CDN_HOSTNAME = "cdn.test.com"
        mock_get_settings.return_value = mock_settings

        service = StorageService()
        
        # Mock httpx Client
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put.return_value = MagicMock(status_code=201)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # Mock UploadFile
            file = MagicMock(spec=UploadFile)
            file.filename = "test-image.png"
            file.read = AsyncMock(return_value=b"fake-image-content")
            file.seek = AsyncMock()

            # Execute
            result = await service.upload_file(file, path="notes")

            # Verify
            assert result["filename"] == "test-image.png"
            assert result["url"] == "https://cdn.test.com/notes/test-image.png"
            assert result["size"] == len(b"fake-image-content")
            
            # Verify HTTP call
            expected_url = "https://storage.bunnycdn.com/test-zone/notes/test-image.png"
            mock_client.put.assert_called_once()
            call_args = mock_client.put.call_args
            assert call_args[0][0] == expected_url
            assert call_args[1]["headers"]["AccessKey"] == "test-key"

@pytest.mark.asyncio
async def test_upload_file_missing_config():
    """Test upload fails when config is missing."""
    with patch("src.services.storage_service.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.BUNNY_CDN_API_KEY = None # Missing
        mock_get_settings.return_value = mock_settings

        service = StorageService()
        file = MagicMock(spec=UploadFile)

        with pytest.raises(HTTPException) as exc:
            await service.upload_file(file)
        
        assert exc.value.status_code == 503
        assert "Storage configuration is missing" in exc.value.detail

@pytest.mark.asyncio
async def test_upload_file_api_error():
    """Test upload fails when BunnyCDN API returns error."""
    with patch("src.services.storage_service.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.BUNNY_CDN_API_KEY = "test-key"
        mock_settings.BUNNY_STORAGE_ZONE = "test-zone"
        mock_settings.BUNNY_CDN_HOSTNAME = "cdn.test.com"
        mock_get_settings.return_value = mock_settings

        service = StorageService()
        
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            # Simulate 401 Unauthorized from Bunny
            mock_client.put.return_value = MagicMock(status_code=401, text="Unauthorized")
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            file = MagicMock(spec=UploadFile)
            file.filename = "test.png"
            file.read = AsyncMock(return_value=b"content")
            file.seek = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await service.upload_file(file)
            
            assert exc.value.status_code == 500
            assert "BunnyCDN upload failed" in exc.value.detail

