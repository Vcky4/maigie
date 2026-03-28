"""
Fetch public web pages server-side for Studio “reader” preview with SSRF protections.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
import nh3
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5

_USER_AGENT = (
    "Mozilla/5.0 (compatible; MaigieStudioPreview/1.0; +https://maigie.ai) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_BLOCK_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".corp",
    ".home",
    ".localdomain",
)

ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "div",
        "span",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "a",
        "strong",
        "b",
        "em",
        "i",
        "blockquote",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "colgroup",
        "col",
        "img",
        "hr",
        "figure",
        "figcaption",
        "cite",
        "small",
        "sub",
        "sup",
        "mark",
    }
)

_PREVIEW_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "title", "lang", "dir"},
    "a": {"href", "title", "rel", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
}


def _host_blocked_by_name(host: str) -> bool:
    h = host.strip().lower().rstrip(".")
    if h in {"localhost", "0.0.0.0", "metadata.google.internal"}:
        return True
    if h.endswith(_BLOCK_HOST_SUFFIXES):
        return True
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", h):
        try:
            ip = ipaddress.ip_address(h)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            )
        except ValueError:
            return True
    return False


def assert_url_safe_for_ssrf(url: str) -> None:
    """Raise ValueError if the URL must not be fetched (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL is missing a host")
    if _host_blocked_by_name(host):
        raise ValueError("Host is not allowed")

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("Could not resolve host") from e

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Resolved address is not allowed")


def _pick_main_node(soup: BeautifulSoup) -> BeautifulSoup:
    for sel in ("article", "main", '[role="main"]'):
        node = soup.select_one(sel)
        if node:
            return BeautifulSoup(str(node), "html.parser")
    if soup.body:
        return BeautifulSoup(str(soup.body), "html.parser")
    return soup


def _strip_unsafe_nodes(soup: BeautifulSoup) -> None:
    for sel in ("script", "style", "noscript", "iframe", "object", "embed", "form", "template"):
        for t in soup.find_all(sel):
            t.decompose()


def _absolutize_urls(fragment: BeautifulSoup, base_url: str) -> None:
    for tag in fragment.find_all("a", href=True):
        tag["href"] = urljoin(base_url, tag["href"])
        tag["target"] = "_blank"
        tag["rel"] = "noopener noreferrer"
    for tag in fragment.find_all("img", src=True):
        tag["src"] = urljoin(base_url, tag["src"])


async def fetch_page_preview_html(url: str) -> tuple[str, str | None]:
    """
    Fetch URL, extract main-ish HTML, sanitize. Returns (html_fragment, title).
    Raises ValueError for client-side issues, RuntimeError for fetch/parse failures.
    """
    assert_url_safe_for_ssrf(url)

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    ) as client:
        async with client.stream("GET", url, headers=headers) as response:
            assert_url_safe_for_ssrf(str(response.url))
            if response.status_code >= 400:
                raise RuntimeError(f"Remote server returned {response.status_code}")
            ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype and ctype not in ("text/html", "application/xhtml+xml"):
                raise RuntimeError("URL is not an HTML page (wrong content type)")

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise RuntimeError("Page is too large to preview")
                chunks.append(chunk)

    raw_bytes = b"".join(chunks)
    if not raw_bytes.strip():
        raise RuntimeError("Empty response")

    text = raw_bytes.decode("utf-8", errors="replace")

    soup = BeautifulSoup(text, "html.parser")
    title_tag = soup.title
    page_title = title_tag.get_text(strip=True) if title_tag else None

    _strip_unsafe_nodes(soup)
    fragment_soup = _pick_main_node(soup)
    _strip_unsafe_nodes(fragment_soup)
    _absolutize_urls(fragment_soup, url)

    body = fragment_soup.body
    inner = body.decode_contents() if body else str(fragment_soup)

    cleaned = nh3.clean(
        inner,
        tags=ALLOWED_TAGS,
        attributes=_PREVIEW_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )

    if not cleaned or not cleaned.strip():
        raise RuntimeError("No readable content could be extracted")

    return cleaned, page_title
