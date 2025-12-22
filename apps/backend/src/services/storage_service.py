"""
Storage service for handling file uploads (BunnyCDN).
"""

import httpx
from fastapi import UploadFile, HTTPException, status
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

            # Construct public URL
            # Assuming cdn_hostname is like "cdn.maigie.com"
            public_url = f"https://{self.cdn_hostname}/{upload_path}"

            return {"filename": filename, "url": public_url, "size": file_size}

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"File upload failed: {str(e)}",
            )
        finally:
            await file.seek(0)  # Reset file pointer if needed elsewhere


storage_service = StorageService()
