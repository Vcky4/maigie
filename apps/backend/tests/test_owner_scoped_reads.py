"""A read of a learner-owned row by primary key, in a function that takes no owner, must be deliberate.

**Why this exists.** Two disclosures were found in the Ask Maigie context enrichment path within two
days of each other — `noteId` (plan §5.5.11) and then `topicId` / `courseId` (§5.5.14) — and both had the
same shape: an id arriving from the client, a `select(Thing).where(Thing.id == thing_id)` with nothing
else in the `where`, and the row's contents going somewhere the learner could read them. The second
survived the fix for the first because the fix was local and the reasoning behind it — "these particular
rows are shared" — was wrong and written down as a comment.

**What this guard covers, and what it does not.** It finds the shape that is dangerous *by construction*:
a by-id read of an owned model inside a function whose parameters mention no owner at all, so nothing in
the function could apply a filter even if it wanted to. Those are almost all repository primitives, which
are legitimate — a repository method may take an id and trust its caller — but only while every caller
authorises. The allowlist below records that judgement per function, with the caller that does the
authorising named, so the judgement can be re-checked instead of re-derived.

It does **not** catch an unfiltered read inside a function that *does* have the owner in scope. §5.5.14
was exactly that: the WebSocket handler had `user.id` available throughout and simply did not use it.
That class needs a per-statement scan, which
`tests/test_chat_context_authorization.py` does for the conversation package. Generalising that scan
repo-wide surfaced 84 statements, of which about fifteen were triaged during the sweep of 2026-08-28 and
all fifteen were sound. **The remaining ~70 are untriaged**, and allowlisting them wholesale here would
launder unreviewed code as reviewed, which is the failure this file exists to prevent. That work is
recorded as open in the plan rather than papered over here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

#: Parameter names that mean the function knows whose data it is handling.
OWNER_PARAMS = {"user_id", "userId", "owner_id", "current_user", "user"}

#: By-id reads of owned rows in owner-less functions that are **intended**, with the reason.
#:
#: Every entry is a repository or service primitive whose callers authorise. The value names the caller
#: that does it. If a caller is added that does not, the entry is wrong and the read becomes a hole — so
#: read the reason before adding a caller, not after.
ALLOWED: dict[str, str] = {
    "domains/intelligence/repository.py::find_chat_session": (
        "Two callers, both authorise: `conversation_service.get_conversation` compares "
        "`session.user_id` and raises `NotFoundError`, and the WebSocket handler goes through "
        "`ask_service.resolve_session_for_turn`, which is where the room-vs-owner rules live."
    ),
    "domains/intelligence/repository.py::find_message": (
        "**No callers at all** as of 2026-08-28. Dead, and listed rather than deleted because deleting "
        "a repository method is a separate decision from this sweep. Anyone wiring it up owes it an "
        "owner filter or an authorising caller — a message id identifies a row in someone's thread."
    ),
    "domains/personal_learning/repository.py::update_exam_prep": (
        "Callers in `exam_prep_service` and `prep_outcome_service` resolve the prep through "
        "`find_exam_prep(prep_id, user_id)` first."
    ),
    "domains/personal_learning/repository.py::update_document": (
        "`document_impl.publish_document` / `unpublish_document` resolve through "
        "`find_document(doc_id, user_id)` first. These toggle a public share link, so the check is "
        "load-bearing: an unowned write here would publish another learner's document."
    ),
    "domains/personal_learning/repository.py::update_flashcard": (
        "`flashcard_service.update_flashcard` writes through `update_flashcard_fields(card_id, "
        "user_id, data)`, which carries the filter itself."
    ),
    "domains/personal_learning/repository.py::update_quiz_session": (
        "Callers are all inside `quiz_engine`, operating on a session it resolved for the learner "
        "earlier in the same flow."
    ),
    "domains/personal_learning/repository.py::update_collection": (
        "`collection_service.update_collection` resolves through `find_collection(collection_id, "
        "user_id)` and raises `NotFoundError` first."
    ),
    "domains/progress/repository.py::update_goal": (
        "`goal_service.update_goal` takes `user_id` and resolves the goal as its owner's; the "
        "lifecycle and prep-outcome callers operate on goals they already loaded per learner."
    ),
    "domains/progress/repository.py::update_block": (
        "`schedule_service.update_block` takes `user_id` and resolves first; the spaced-repetition "
        "callers pass a block reached from a review they already own."
    ),
    "domains/progress/repository.py::find_session": (
        "`analytics_service.stop_study_session` compares `session.user_id != user_id` and raises "
        "`NotFoundError`. The check is after the read rather than in the `where`, which is weaker but "
        "correct."
    ),
    "domains/progress/repository.py::update_review": (
        "`spaced_repetition_impl` resolves the review for the learner before updating it."
    ),
    "domains/notifications/repository.py::claim_due_deliveries": (
        "No id enters this at all: the batch is selected by channel, status, and time, and the `.id ==` "
        "the scan matches is the delivery→notification→installation join, not a lookup. Its only caller "
        "is `dispatcher.dispatch_due`, reached solely from the argument-less Celery beat task "
        "`notifications.dispatch_mobile_push`. Rows go to the push provider addressed to the row's own "
        "owner, never into a response, so there is no learner to disclose to."
    ),
    "domains/notifications/repository.py::record_ticket_result": (
        "The `delivery_id` is one this process claimed moments earlier in `dispatcher.dispatch_due`, not "
        "one off a request — the only caller is that dispatch loop, under the beat task "
        "`notifications.dispatch_mobile_push`. It writes the provider outcome and returns nothing, so "
        "the read cannot disclose a row: an owner filter would need an owner the worker does not have "
        "and does not act on behalf of."
    ),
    "domains/notifications/repository.py::record_receipt": (
        "Same shape as `record_ticket_result`, one stage later: the `delivery_id` comes from "
        "`accepted_for_receipts` inside `dispatcher.reconcile_receipts`, whose only entry point is the "
        "argument-less beat task `notifications.reconcile_expo_receipts`. It records the Expo receipt "
        "against the delivery and its attempt row and returns nothing to any caller that could relay it "
        "to a learner."
    ),
    "domains/notifications/repository.py::claim_due_email_deliveries": (
        "The email counterpart of `claim_due_deliveries`, and unscoped for the same reason: no id "
        "enters it, the batch is chosen by channel, status and time, and the `.id ==` is the "
        "delivery→notification join rather than a lookup. Its only caller is "
        "`email_dispatcher.dispatch_due_email`, reached solely from the argument-less beat task "
        "`notifications.dispatch_email`. Each claimed row is emailed to its own owner's address, which "
        "the dispatcher re-reads per row, so nothing crosses between learners and nothing reaches a "
        "response."
    ),
    "domains/notifications/repository.py::record_email_result": (
        "The `delivery_id` is one this process claimed moments earlier in "
        "`email_dispatcher.dispatch_due_email`, not one off a request — the only caller is that "
        "dispatch loop, under the beat task `notifications.dispatch_email`. It writes the provider "
        "outcome and its attempt row and returns nothing, so an owner filter would need an owner the "
        "worker does not have and does not act on behalf of."
    ),
}


def _owned_models() -> set[str]:
    """ORM classes with a `user_id` column — the rows that belong to one learner."""
    owned: set[str] = set()
    for path in SRC.rglob("db_models.py"):
        text = path.read_text(encoding="utf-8")
        for node in ast.parse(text).body:
            if isinstance(node, ast.ClassDef):
                source = ast.get_source_segment(text, node) or ""
                if re.search(r"\buser_id\b\s*:\s*Mapped", source):
                    owned.add(node.name)
    return owned


OWNED_MODELS = _owned_models()


def _by_id_reads_without_an_owner() -> list[str]:
    """`module::function` for every by-id read of an owned model in a function with no owner in scope."""
    found: list[str] = []
    owned = OWNED_MODELS

    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "select(" not in text:
            continue

        for func in ast.walk(ast.parse(text)):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue

            params = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
            if params & OWNER_PARAMS:
                continue

            body = ast.get_source_segment(text, func) or ""
            # An owner mentioned anywhere in the body means the function can and probably does filter.
            if any(marker in body for marker in ("user_id", "userId", "owner_id")):
                continue
            if not re.search(r"\.id\s*==", body):
                continue

            for call in ast.walk(func):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "select"
                ):
                    continue
                if {a.id for a in call.args if isinstance(a, ast.Name)} & owned:
                    relative = path.relative_to(SRC).as_posix()
                    found.append(f"{relative}::{func.name}")
                    break

    return found


UNSCOPED_READS = sorted(set(_by_id_reads_without_an_owner()))


def test_the_model_inventory_is_not_empty():
    """Guards the guard. An empty `OWNED_MODELS` would make every check below pass vacuously, which is
    indistinguishable from the codebase being safe."""
    assert len(OWNED_MODELS) > 20, (
        f"only {len(OWNED_MODELS)} owned models found. The `user_id: Mapped` detection has probably "
        "stopped matching — check whether the column declaration style changed."
    )


def test_the_scan_finds_the_reads_it_is_meant_to_check():
    assert UNSCOPED_READS, (
        "no by-id reads of owned rows found in owner-less functions. Either every repository now takes "
        "an owner — in which case delete this file and celebrate — or the scan has stopped matching."
    )


@pytest.mark.parametrize("location", UNSCOPED_READS)
def test_every_unscoped_by_id_read_is_accounted_for(location: str) -> None:
    assert location in ALLOWED, (
        f"{location} reads a learner-owned row by id, and its function takes no owner, so it cannot "
        "filter.\n\n"
        "That is the shape of the two Ask Maigie disclosures: an id off the request, a `where(id == "
        "...)` with nothing else in it, and the row's contents reaching the learner. It is legitimate "
        "for a repository primitive whose callers all authorise.\n\n"
        "Either add the owner to the read — `find_thing(thing_id, user_id)` — or, if every caller "
        "authorises, add an entry to `ALLOWED` in this file naming the caller that does it. Do not add "
        "the entry without checking; the entry is the claim."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """An entry for a read that no longer exists is a claim about code that is gone, and it makes the
    list look more reviewed than it is."""
    stale = sorted(set(ALLOWED) - set(UNSCOPED_READS))
    assert stale == [], (
        f"these allowlist entries no longer match any read: {stale}. Remove them — the read was fixed "
        "or moved, and an entry that matches nothing is a stale justification."
    )


def test_every_allowlist_entry_gives_a_reason() -> None:
    """A bare exemption is how an allowlist becomes a way of turning the guard off."""
    for location, reason in ALLOWED.items():
        assert len(reason) > 60, f"{location} is exempted without a real reason"
