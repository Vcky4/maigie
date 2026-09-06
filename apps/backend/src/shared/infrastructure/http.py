"""
Shared HTTP client.

Provides a reusable httpx.AsyncClient for external API calls.
Configures timeouts, retries, and connection pooling globally.
"""

import httpx

# Default timeout for external API calls (30 seconds)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def create_http_client(
    base_url: str = "",
    timeout: httpx.Timeout | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Create a configured async HTTP client.

    Args:
        base_url: Optional base URL for all requests.
        timeout: Custom timeout configuration.
        headers: Default headers to include in all requests.

    Returns:
        Configured httpx.AsyncClient instance.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout or DEFAULT_TIMEOUT,
        headers=headers or {},
        follow_redirects=True,
    )
