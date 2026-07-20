"""Storage service instance — wraps the StorageClient for domain use."""

from src.config import get_settings


class StorageServiceInstance:
    """Preconfigured storage service for BunnyCDN."""

    def __init__(self):
        self._initialized = False
        self.api_key: str | None = None
        self.storage_zone: str | None = None
        self.base_url: str = ""
        self.public_url_base: str = ""
        self.cdn_hostname: str = ""

    def _ensure_init(self):
        if not self._initialized:
            try:
                settings = get_settings()
                self.api_key = getattr(settings, "BUNNY_STORAGE_API_KEY", None)
                self.storage_zone = getattr(settings, "BUNNY_STORAGE_ZONE", None)
                self.cdn_hostname = getattr(settings, "BUNNY_CDN_HOSTNAME", "")
                if self.storage_zone:
                    self.base_url = f"https://storage.bunnycdn.com/{self.storage_zone}"
                self.public_url_base = (
                    f"https://{self.cdn_hostname}" if self.cdn_hostname else ""
                )
            except Exception:
                pass
            self._initialized = True


storage_service = StorageServiceInstance()
