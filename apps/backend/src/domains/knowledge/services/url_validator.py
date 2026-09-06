"""Resolve and check URLs before they are stored as resources.

This exists because a recommended URL is a claim about the world, and the two ways we get
one are both unreliable in different ways:

- **The model writes it.** A URL is a string, predicting strings is what the model does,
  and a plausible URL is as easy to produce as a real one. Ungrounded recommendations were
  frequently 404s.
- **Grounding provides it.** Search grounding returns real results, but the URI on a
  grounding chunk is usually a
  ``vertexaisearch.cloud.google.com/grounding-api-redirect/...`` indirection rather than
  the destination. Storing that would put a Google redirect in the learner's library
  instead of the page, and those redirects are not durable.

Both problems have the same answer: follow the URL and see where it lands. So resolution
and validation are one step, which is why they are one function.

What this is not: a check that the page is *about* the topic. A live homepage is still
live. It rules out the worst outcome — a permanent library row pointing at nothing — and
nothing more.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: Per-URL timeout. Short on purpose: this runs inside a request the learner is waiting on,
#: and a slow host is not worth holding a recommendation list for.
_TIMEOUT_SECONDS = 6.0

#: How many URLs to check at once. Bounded so a twenty-item list cannot open twenty
#: sockets, while still being far faster than serial checks.
_MAX_CONCURRENCY = 6

#: Redirects to follow. Grounding redirects add one hop, and shorteners can add more.
_MAX_REDIRECTS = 5

#: Sent because a bare client gets blocked or served a challenge page by enough sites to
#: skew the results. Honest about being a bot.
_HEADERS = {
    "User-Agent": "Maigie-LinkCheck/1.0 (+https://maigie.com; resource link validation)",
    "Accept": "*/*",
}


@dataclass(frozen=True)
class CheckedUrl:
    """The outcome of resolving one URL."""

    #: The URL as given.
    original: str
    #: Where it ended up after redirects, or ``None`` when it could not be reached.
    resolved: str | None
    status: int | None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.resolved is not None


async def _check_one(client: httpx.AsyncClient, url: str) -> CheckedUrl:
    """Resolve a single URL, preferring HEAD and falling back to GET.

    HEAD first because it is cheap. The fallback is not optional: plenty of servers answer
    HEAD with 403, 405 or a bare 404 while serving the same path perfectly well over GET,
    and treating those as dead would discard good resources. The GET is capped by the same
    timeout and its body is never read.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return CheckedUrl(original=url, resolved=None, status=None, reason="not an http url")

    try:
        response = await client.head(url)
        if response.status_code in (403, 405, 501) or response.status_code >= 500:
            response = await client.get(url)
    except httpx.HTTPError as error:
        return CheckedUrl(
            original=url,
            resolved=None,
            status=None,
            reason=type(error).__name__,
        )
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("Unexpected error checking %s: %s", url, error)
        return CheckedUrl(original=url, resolved=None, status=None, reason="error")

    if response.status_code >= 400:
        return CheckedUrl(
            original=url,
            resolved=None,
            status=response.status_code,
            reason=f"http {response.status_code}",
        )

    # `str(response.url)` is the destination after redirects, which is the whole point for
    # grounding URIs — the original is an indirection we do not want to store.
    return CheckedUrl(original=url, resolved=str(response.url), status=response.status_code)


async def check_urls(urls: list[str]) -> dict[str, CheckedUrl]:
    """Resolve a batch of URLs concurrently, keyed by the URL as given.

    Never raises. A URL that cannot be checked comes back with ``ok`` false and a reason;
    the caller decides what to do about it. Failing the whole batch because one host is
    down would mean one bad link costs the learner the entire list.
    """
    if not urls:
        return {}

    unique = list(dict.fromkeys(urls))
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        headers=_HEADERS,
    ) as client:

        async def guarded(url: str) -> CheckedUrl:
            async with semaphore:
                return await _check_one(client, url)

        results = await asyncio.gather(*(guarded(url) for url in unique), return_exceptions=True)

    checked: dict[str, CheckedUrl] = {}
    for url, result in zip(unique, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("URL check raised for %s: %s", url, result)
            checked[url] = CheckedUrl(original=url, resolved=None, status=None, reason="error")
        else:
            checked[url] = result
    return checked
