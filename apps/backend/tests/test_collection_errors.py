"""Collection 404s, and the doubled word that was in every one of them.

`NotFoundError`'s first argument is a **resource name**, not a message: the constructor builds
`f"{resource} not found"` and appends the id when one is passed. Every call in
`collection_service.py` passed the finished sentence `"Collection not found"`, so the published body
read `"Collection not found not found"` — on all eight paths, which is every failure mode collections
have. Every other domain gets this right (`NotFoundError("Document", doc_id)`), so the fix was to
match them, and passing the id makes the message useful rather than merely grammatical.

Nothing caught it because nothing asserted on the *text*. A test that only checks `status_code == 404`
passes either way, and that is what a 404 is usually worth testing for — which is the argument for
this file being about the message specifically.

These are unit tests on the exception, deliberately, and not HTTP round trips. Each service call
raises before it needs a database, but only after a repository lookup that would; asserting on the
error the constructor produces tests the thing that was broken without needing Postgres, and so
cannot skip on a machine without one.
"""

from __future__ import annotations

import re

import pytest

from src.shared.exceptions import NotFoundError


def test_not_found_error_appends_the_phrase_itself():
    """The constructor's contract, which is what the call sites got wrong."""
    assert NotFoundError("Collection").message == "Collection not found"
    assert NotFoundError("Collection", "abc123").message == "Collection not found: abc123"

    # And the shape of the mistake: handing it a finished sentence doubles the phrase.
    assert NotFoundError("Collection not found").message == "Collection not found not found"


@pytest.mark.parametrize(
    "resource",
    ["Collection", "Collection item"],
)
def test_collection_resources_read_once(resource: str):
    """No collection error says "not found" twice."""
    message = NotFoundError(resource, "xyz").message
    assert message.count("not found") == 1, message
    assert message.startswith(resource)


def test_no_collection_call_site_passes_a_sentence():
    """Guards the call sites, not just the constructor.

    Reading the source rather than invoking the service, because every one of these raises only after
    a repository lookup — so provoking all eight through their functions would need a database, and a
    database-backed test skips where there is none. The defect was textual and lives in the source, so
    the source is what is checked.

    Generalised past collections to the whole tree: this was not a collections idea, it was a
    misreading of a shared constructor, and nothing stops the next domain making it.
    """
    import pathlib

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []

    # First argument is a plain string literal that already contains "not found".
    pattern = re.compile(r"""NotFoundError\(\s*["'][^"']*not\s+found""", re.IGNORECASE)

    for path in sorted(source_root.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path}:{number}: {line.strip()}")

    assert not offenders, (
        "NotFoundError's first argument is a resource name — it appends ' not found' itself:\n"
        + "\n".join(offenders)
    )
