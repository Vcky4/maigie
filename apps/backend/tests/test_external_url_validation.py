"""Learner-supplied URLs, on the way in.

`SavedResource.url` is rendered into an `href` by three surfaces — the web resources library, the web
collection detail page and mobile's in-app reader — and nothing checked its scheme. A resource saved
with `javascript:alert(document.cookie)` executed on click, in the app's own origin.

Scope of the original issue: saved resources are `userId`-scoped and collections are not shareable, so
this was self-XSS rather than cross-user. That bounds the severity; it does not make storing an
executable address acceptable, and the bound depends on collections staying private.

An allowlist, not a denylist. The interesting cases below are the ones a denylist of `javascript:` and
`data:` would let through: a different scheme entirely, a mixed-case one, and the control characters a
browser strips *before* parsing — `java\tscript:alert(1)` reaches the DOM as `javascript:` while
`urlparse` reads it as a schemeless relative path.

Refusing on write does not make reading safe on its own — rows written before this validation are still
in the database — so both clients also guard at render time. These tests cover the write half.
"""

from __future__ import annotations

import pydantic
import pytest

from src.domains.knowledge import models as knowledge_models
from src.domains.personal_learning import models as pl_models
from src.shared.validation import is_safe_external_url

REFUSED = [
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param("JavaScript:alert(1)", id="javascript-mixed-case"),
    pytest.param("JAVASCRIPT:alert(1)", id="javascript-upper-case"),
    pytest.param("java\tscript:alert(1)", id="javascript-embedded-tab"),
    pytest.param("java\nscript:alert(1)", id="javascript-embedded-newline"),
    pytest.param("java\rscript:alert(1)", id="javascript-embedded-return"),
    pytest.param("\x00javascript:alert(1)", id="javascript-leading-null"),
    pytest.param("  javascript:alert(1)", id="javascript-leading-space"),
    pytest.param("data:text/html,<script>alert(1)</script>", id="data-html"),
    pytest.param("vbscript:msgbox(1)", id="vbscript"),
    pytest.param("file:///etc/passwd", id="file"),
    pytest.param("//evil.example/path", id="scheme-relative"),
    pytest.param("/relative/path", id="relative"),
    pytest.param("http:///no-host", id="empty-host"),
    pytest.param("", id="empty-string"),
    pytest.param("   ", id="whitespace-only"),
]

ACCEPTED = [
    "https://example.dev",
    "https://example.dev/path?query=1#fragment",
    "http://example.dev",
    "https://sub.example.dev:8443/deep/path",
]


@pytest.mark.parametrize("value", REFUSED)
def test_is_safe_external_url_refuses(value: str):
    assert is_safe_external_url(value) is False


@pytest.mark.parametrize("value", ACCEPTED)
def test_is_safe_external_url_accepts_http_and_https(value: str):
    assert is_safe_external_url(value) is True


@pytest.mark.parametrize("value", REFUSED)
def test_saved_resource_create_refuses(value: str):
    """A `422` naming the field, rather than a stored address a client will later render."""
    with pytest.raises(pydantic.ValidationError):
        pl_models.SavedResourceCreate(title="A resource", url=value, sourceType="web")


@pytest.mark.parametrize("value", ACCEPTED)
def test_saved_resource_create_accepts(value: str):
    resource = pl_models.SavedResourceCreate(title="A resource", url=value, sourceType="web")
    assert resource.url == value


def test_saved_resource_create_still_allows_no_url():
    """`SavedResource.url` is nullable — a resource saved from a source with no address is valid."""
    resource = pl_models.SavedResourceCreate(title="A lecture", sourceType="upload")
    assert resource.url is None


def test_surrounding_whitespace_is_trimmed_rather_than_refused():
    """A pasted link often carries trailing whitespace, which is not an attack."""
    resource = pl_models.SavedResourceCreate(
        title="A resource", url="  https://example.dev/x  ", sourceType="web"
    )
    assert resource.url == "https://example.dev/x"


@pytest.mark.parametrize("value", REFUSED)
def test_knowledge_resource_create_refuses(value: str):
    """The other client-writable URL that reaches an `href`, via the resource browse surfaces."""
    with pytest.raises(pydantic.ValidationError):
        knowledge_models.ResourceCreate(title="A resource", url=value)


def test_knowledge_resource_create_accepts_https():
    assert knowledge_models.ResourceCreate(title="A resource", url="https://example.dev").url == (
        "https://example.dev"
    )
