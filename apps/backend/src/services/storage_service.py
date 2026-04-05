"""
Storage service for handling file uploads (BunnyCDN).
"""

from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, UploadFile, status

from src.config import get_settings


class StorageService:
    """
    Abstracts storage operations. currently implemented for BunnyCDN.
    """

    def __init__(self):
        self.settings = get_settings()
        self.api_key = self.settings.BUNNY_CDN_API_KEY
        self.storage_zone = self.settings.BUNNY_STORAGE_ZONE
        self.cdn_hostname = self.settings.BUNNY_CDN_HOSTNAME
        raw_base = (self.settings.BUNNY_PUBLIC_URL_BASE or "").strip().rstrip("/")
        self.public_url_base = raw_base if raw_base else None

        # Base URL for BunnyCDN Storage API (Germany region default, adjust if needed)
        self.base_url = f"https://uk.storage.bunnycdn.com/{self.storage_zone}"

    async def upload_file(self, file: UploadFile, path: str = "") -> dict:
        """
        Upload a file to BunnyCDN storage.

        Args:
            file: The FastAPI UploadFile object.
            path: Optional subdirectory path (e.g., "notes/images/").

        Returns:
            dict: { "filename": str, "url": str, "size": int }
        """
        if not self.api_key or not self.storage_zone:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage configuration is missing.",
            )

        # Sanitize filename (basic)
        filename = file.filename.replace(" ", "_")

        # Construct full upload path
        upload_path = f"{path.strip('/')}/{filename}" if path else filename
        upload_url = f"{self.base_url}/{upload_path}"

        headers = {
            "AccessKey": self.api_key,
            "Content-Type": "application/octet-stream",  # BunnyCDN recommends this or actual type
        }

        try:
            # Read file content
            content = await file.read()
            file_size = len(content)

            async with httpx.AsyncClient() as client:
                response = await client.put(upload_url, headers=headers, content=content)

                if response.status_code != 201:
                    raise Exception(f"BunnyCDN upload failed: {response.text}")

            # Public URL: optional pull-zone base, else custom hostname
            if self.public_url_base:
                public_url = f"{self.public_url_base}/{upload_path}"
            else:
                public_url = f"https://{self.cdn_hostname}/{upload_path}"

            return {"filename": filename, "url": public_url, "size": file_size}

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"File upload failed: {str(e)}",
            )
        finally:
            await file.seek(0)  # Reset file pointer if needed elsewhere

    async def delete_file(self, url: str) -> bool:
        """
        Delete a file from BunnyCDN storage by its public URL.

        Args:
            url: The public CDN URL of the file to delete.

        Returns:
            bool: True if deleted successfully, False otherwise.
        """
        if not self.api_key or not self.storage_zone:
            return False

        try:
            parsed = urlparse(url)
            file_path = (parsed.path or "").lstrip("/")
            if not file_path:
                return False
            delete_url = f"{self.base_url}/{file_path}"

            headers = {"AccessKey": self.api_key}

            async with httpx.AsyncClient() as client:
                response = await client.delete(delete_url, headers=headers)
                return response.status_code == 200

        except Exception as e:
            print(f"Failed to delete file from storage: {e}")
            return False

    def chat_images_storage_path(self, public_url: str) -> str | None:
        """If this URL points at a chat upload in storage, return the storage-relative path."""
        try:
            parsed = urlparse(public_url)
            path = (parsed.path or "").lstrip("/")
            if path.startswith("chat-images/"):
                return path
        except Exception:
            pass
        return None

    async def fetch_object_bytes(self, relative_path: str) -> tuple[bytes, str] | None:
        """
        Download an object from Bunny storage API (works when public CDN TLS is misconfigured).
        relative_path: e.g. chat-images/foo.png
        """
        if not self.api_key or not self.storage_zone:
            return None
        safe = relative_path.lstrip("/")
        if ".." in safe:
            return None
        get_url = f"{self.base_url}/{safe}"
        headers = {"AccessKey": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(get_url, headers=headers)
                if response.status_code != 200:
                    return None
                ct = response.headers.get("content-type", "application/octet-stream")
                return response.content, ct
        except Exception as e:
            print(f"Storage fetch failed for {safe}: {e}")
            return None

    async def fetch_public_chat_image_bytes(self, public_url: str) -> tuple[bytes, str] | None:
        path = self.chat_images_storage_path(public_url)
        if not path:
            return None
        return await self.fetch_object_bytes(path)


storage_service = StorageService()
