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
from urllib.parse import unquote, urljoin, urlparse

import httpx
import nh3
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
MAX_PDF_BYTES = 12_000_000
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

# Do not whitelist "rel" on <a> when using link_rel=... — nh3 panics (see ammonia docs).
_PREVIEW_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "title", "lang", "dir"},
    "a": {"href", "title", "target"},
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
    for tag in fragment.find_all("img", src=True):
        tag["src"] = urljoin(base_url, tag["src"])


def _path_suggests_pdf(url: str) -> bool:
    p = urlparse(url).path.lower().rstrip("/")
    return p.endswith(".pdf")


def _max_preview_bytes(url: str, primary_mime: str) -> int:
    if primary_mime in ("application/pdf", "application/x-pdf"):
        return MAX_PDF_BYTES
    if primary_mime == "application/octet-stream" and _path_suggests_pdf(url):
        return MAX_PDF_BYTES
    if _path_suggests_pdf(url):
        return MAX_PDF_BYTES
    return MAX_BYTES


def _is_pdf_magic(data: bytes) -> bool:
    return len(data) >= 5 and data[:5] == b"%PDF-"


def _title_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    # filename*=UTF-8''name.pdf
    m = re.search(r"filename\*=(?:UTF-8''|)([^;\s]+)", header, re.IGNORECASE)
    if m:
        raw = m.group(1).strip().strip("'\"")
        name = unquote(raw)
        if name:
            base = name.rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0] if "." in base else base
            return stem.replace("_", " ").strip() or None
    m = re.search(r'filename="([^"]+)"', header, re.IGNORECASE)
    if not m:
        m = re.search(r"filename=([^;\s]+)", header, re.IGNORECASE)
    if m:
        name = unquote(m.group(1).strip().strip("'\""))
        if name:
            base = name.rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0] if "." in base else base
            return stem.replace("_", " ").strip() or None
    return None


def _fragment_inner_html(fragment_soup: BeautifulSoup) -> str:
    body = fragment_soup.body
    if body:
        return body.decode_contents()
    return str(fragment_soup)


def _main_content_html_trafilatura(html: str, page_url: str) -> tuple[str | None, str | None]:
    """
    Extract main article/body HTML (drops most global nav, promos, footers).
    Returns (html_string_or_none, extracted_title_or_none).
    """
    try:
        main_html = trafilatura.extract(
            html,
            url=page_url,
            output_format="html",
            include_links=True,
            include_images=True,
            include_tables=True,
            include_formatting=True,
            include_comments=False,
            favor_precision=True,
        )
    except Exception as e:
        logger.warning(
            "trafilatura extract failed, falling back to heuristic DOM pick",
            extra={"url": page_url[:120], "error": str(e)},
        )
        return None, None

    if not main_html or not main_html.strip():
        return None, None

    meta_title: str | None = None
    try:
        meta = trafilatura.extract_metadata(html, default_url=page_url)
        if meta is not None and getattr(meta, "title", None):
            t = str(meta.title).strip()
            meta_title = t or None
    except Exception as e:
        logger.debug("trafilatura metadata extraction skipped: %s", e)

    return main_html.strip(), meta_title


def _html_preview_from_bytes(raw_bytes: bytes, page_url: str) -> tuple[str, str | None]:
    """Parse downloaded HTML bytes into sanitized reader fragment and title."""
    text = raw_bytes.decode("utf-8", errors="replace")

    soup = BeautifulSoup(text, "html.parser")
    title_tag = soup.title
    page_title = title_tag.get_text(strip=True) if title_tag else None

    main_html, extracted_title = _main_content_html_trafilatura(text, page_url)
    if extracted_title:
        page_title = extracted_title

    if main_html:
        fragment_soup = BeautifulSoup(main_html, "html.parser")
        _strip_unsafe_nodes(fragment_soup)
        _absolutize_urls(fragment_soup, page_url)
        inner = _fragment_inner_html(fragment_soup)
    else:
        _strip_unsafe_nodes(soup)
        fragment_soup = _pick_main_node(soup)
        _strip_unsafe_nodes(fragment_soup)
        _absolutize_urls(fragment_soup, page_url)
        inner = _fragment_inner_html(fragment_soup)

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


async def fetch_page_preview_html(url: str) -> tuple[str, str | None]:
    """
    Fetch URL, extract main-ish HTML, sanitize. Returns (html_fragment, title).
    Raises ValueError for client-side issues, RuntimeError for fetch/parse failures.
    """
    ctype, title, html, pdf_bytes = await fetch_studio_preview(url)
    if ctype != "text/html" or html is None:
        raise RuntimeError("URL is not an HTML page (wrong content type)")
    if pdf_bytes:
        raise RuntimeError("URL is not an HTML page (wrong content type)")
    return html, title


async def fetch_studio_preview(
    url: str,
) -> tuple[str, str | None, str | None, bytes | None]:
    """
    Fetch URL with SSRF checks. Returns
    (content_type, title, html_or_none, pdf_bytes_or_none).
    content_type is ``text/html`` or ``application/pdf``.
    """
    assert_url_safe_for_ssrf(url)

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,application/x-pdf,*/*;q=0.1",
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

            final_url = str(response.url)
            ctype_header = response.headers.get("content-type") or ""
            primary = ctype_header.split(";")[0].strip().lower()
            content_disposition = response.headers.get("content-disposition")
            max_bytes = _max_preview_bytes(final_url, primary)

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("Resource is too large to preview")
                chunks.append(chunk)

    raw_bytes = b"".join(chunks)
    if not raw_bytes.strip():
        raise RuntimeError("Empty response")

    cd_title = _title_from_content_disposition(content_disposition)

    if _is_pdf_magic(raw_bytes):
        title = cd_title
        return "application/pdf", title, None, raw_bytes

    if primary in ("application/pdf", "application/x-pdf"):
        raise RuntimeError("Response claimed to be a PDF but body is not valid PDF data")

    if primary == "application/octet-stream" and _path_suggests_pdf(final_url):
        raise RuntimeError("Expected a PDF but the response was not valid PDF data")

    if primary not in ("", "text/html", "application/xhtml+xml"):
        raise RuntimeError("URL is not an HTML page or PDF (wrong content type)")

    html, page_title = _html_preview_from_bytes(raw_bytes, final_url)
    title = page_title or cd_title
    return "text/html", title, html, None
