"""Every note the chat pipeline reads must be read as its owner's.

`noteId`, `topicId`, `courseId` and `reviewItemId` arrive on the client's message context and are used
to enrich the prompt. Topics and courses are catalogue rows and are shared by design. **A note is
not.** Its title, summary and full body go into the prompt and are answered about, so a note read
without an owner filter is a read of someone else's private writing.

That is not hypothetical. The enrichment path fetched notes with
`select(Note).where(Note.id == note_id)` and no owner filter, while the review branch immediately
above it filtered on `user_id` — which is exactly what made the omission look deliberate. Any learner
could put another learner's note id on a turn and have `noteTitle`, `noteSummary` and `noteContent`
injected into their prompt.

The fix routes those reads through `personal_learning_repo.find_note(note_id, user_id)`, which is the
repository's canonical by-id read and already applies the filter. This guard is a source scan rather
than a behavioural test because the enrichment body is still inline in
`register_chat_websocket_routes` and cannot be driven without a live database, a live socket and a
live model. A scan that runs is worth more than a behavioural test that is skipped — and the thing
worth pinning is a property of the *query*, which is visible in the source.

Delete this in favour of behavioural tests once the enrichment body moves into `ask_service` and its
reads are injectable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CONVERSATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "domains"
    / "intelligence"
    / "conversation"
)

#: Names the `Note` SQLAlchemy model is imported under in this package.
NOTE_MODEL_NAMES = {"Note", "NoteModel"}

#: What an owner-scoped read looks like. `find_note` carries the filter itself.
OWNERSHIP_MARKERS = ("user_id", "find_note")


def _statements_selecting_notes() -> list[tuple[str, int, str]]:
    """Return (file, line, source) for every statement containing a `select(<Note model>)` call."""
    found: list[tuple[str, int, str]] = []

    for path in sorted(CONVERSATION.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue

            selects_note = False
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if not (isinstance(func, ast.Name) and func.id == "select"):
                    continue
                if any(
                    isinstance(arg, ast.Name) and arg.id in NOTE_MODEL_NAMES
                    for arg in inner.args
                ):
                    selects_note = True
                    break

            if not selects_note:
                continue

            segment = ast.get_source_segment(source, node) or ""
            # Only the innermost statement, so a `select` nested in a big `async with` is attributed
            # to its own assignment rather than to the whole block.
            if any(
                isinstance(child, ast.stmt)
                and child is not node
                and any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name)
                    and c.func.id == "select"
                    and any(
                        isinstance(a, ast.Name) and a.id in NOTE_MODEL_NAMES for a in c.args
                    )
                    for c in ast.walk(child)
                )
                for child in ast.walk(node)
            ):
                continue

            found.append((path.name, node.lineno, segment))

    return found


_NOTE_READS = _statements_selecting_notes()


def test_the_scan_finds_the_note_reads_it_is_meant_to_check():
    """Guards against the scan silently matching nothing and passing vacuously — which would be
    indistinguishable from the code being safe."""
    assert _NOTE_READS, (
        "no `select(Note)` statements found in the conversation package. If the enrichment reads "
        "moved, point this scan at their new home or replace it with behavioural tests."
    )


@pytest.mark.parametrize(
    "filename,lineno,source",
    _NOTE_READS,
    ids=[f"{name}:{line}" for name, line, _ in _NOTE_READS],
)
def test_every_note_read_is_owner_scoped(filename: str, lineno: int, source: str) -> None:
    assert any(marker in source for marker in OWNERSHIP_MARKERS), (
        f"{filename}:{lineno} reads Note without an owner filter.\n\n"
        "A note's title, summary and body reach the prompt, so an unfiltered read by an id taken "
        "from the client's context exposes another learner's private note. Add "
        "`Note.user_id == user.id`, or use `personal_learning_repo.find_note(note_id, user_id)`.\n\n"
        f"{source}"
    )


def test_note_by_id_reads_go_through_the_repository() -> None:
    """The two by-id reads in the enrichment path use `find_note` rather than hand-rolling the filter.

    Two owner-filtered paths are two things to keep correct; the hole existed because one of them was
    written by hand and the filter was left off.
    """
    handler = (CONVERSATION / "websocket_handler.py").read_text(encoding="utf-8")
    assert handler.count("personal_learning_repo.find_note(") >= 2, (
        "expected the note-by-id enrichment reads to go through "
        "`personal_learning_repo.find_note`, which carries the owner filter"
    )
