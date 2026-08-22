"""Validation shared by more than one domain.

Lives here rather than in one domain for the same reason as ``CamelModel``: two domains need it, and a
copy in each is how the two would drift.
"""

from urllib.parse import urlparse

# The only schemes a learner-supplied address may carry.
#
# Deliberately an allowlist. A denylist of `javascript:` and `data:` would miss `vbscript:`, the
# control characters browsers strip before parsing (`java\tscript:`), and whatever the next one is.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Stripped before the scheme is read, because browsers strip them too.
#
# `java\tscript:alert(1)` and a leading newline both parse as `javascript:` in a browser while
# `urlparse` sees a relative path with no scheme at all — so checking the raw string would pass
# something the browser then executes.
_STRIPPED_CHARACTERS = "".join(chr(code) for code in range(0x20)) + "\x7f" + " "


def is_safe_external_url(value: str) -> bool:
    """Whether a string is safe to hand a client to put in a link.

    ``http`` and ``https`` only. Everything else is refused, including the `javascript:` and `data:`
    schemes that turn a stored address into script execution on whoever clicks it, and including
    scheme-relative (`//evil.example`) and relative addresses — a saved resource is somewhere else on
    the internet, so an address without a host is not one.

    Marginally stricter than the clients' read-time guard, deliberately. ``urlparse`` gives
    ``http:///nohost`` an empty netloc so it is refused here, while a browser's WHATWG parser normalises
    it to ``http://nohost/`` and the clients accept it. Strict on the way in, tolerant of anything
    genuinely safe on the way out — a row already stored that way still renders as a working link.
    """
    if not value:
        return False

    candidate = value.strip(_STRIPPED_CHARACTERS)
    if not candidate:
        return False

    try:
        parsed = urlparse(candidate)
    except ValueError:
        # `urlparse` raises on a malformed IPv6 literal, among others.
        return False

    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return False

    # `http:///path` parses with an empty netloc and is not a reachable address.
    return bool(parsed.netloc)


def require_safe_external_url(value: str | None) -> str | None:
    """Pydantic field validator for a learner-supplied link. Passes ``None`` through.

    Raises ``ValueError`` so FastAPI answers `422` with the field named, rather than storing an address
    that a client will later render into an ``href``.

    Refusing on the way in does not make the read path safe on its own: rows written before this
    existed are still in the database, and this only covers the fields it is attached to. Clients guard
    at render time as well — see ``lib/urls.ts`` on web and ``resource-viewer.tsx`` on mobile.
    """
    if value is None:
        return None
    if not is_safe_external_url(value):
        raise ValueError("URL must be an absolute http:// or https:// address")
    return value.strip(_STRIPPED_CHARACTERS)
