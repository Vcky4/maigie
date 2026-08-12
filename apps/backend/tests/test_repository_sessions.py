"""Tests for the read/write session split in the personal-learning repository.

Reads used to go through `_use_session`, which opens an implicit transaction and
commits it on exit, and the pool then reset the connection with a rollback. A
single `SELECT COUNT(*)` cost ~1830 ms against a ~349 ms round trip — about five
round trips for one query. `_read_session` runs reads under `AUTOCOMMIT`, so no
transaction is opened and there is nothing to commit or unwind; measured at ~2.7
round trips (`scripts/measure_read_session.py`).

Two things must stay true, and neither is visible at a call site:

- **A caller-supplied session is never reconfigured.** Inside a `unit_of_work` the
  caller owns the transaction, a read must see that transaction's uncommitted
  writes, and switching isolation level mid-transaction is an error.
- **No method that writes uses the read path.** A write under `AUTOCOMMIT` would
  not be wrong here — each statement self-commits — but it would silently break
  `unit_of_work`'s all-or-nothing guarantee for anything converted by mistake. The
  conversion was mechanical over 65 methods, so this is asserted over the source
  rather than trusted.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import ast
import inspect
from pathlib import Path

import pytest
from sqlalchemy import func, select

from src.domains.personal_learning import repository as repository_module
from src.domains.personal_learning.db_models import PrepTopic
from src.domains.personal_learning.repository import PersonalLearningRepository

# Anything that mutates. `_read_session` opens no transaction, so a method doing
# any of these through it would escape `unit_of_work`.
WRITE_MARKERS = (
    "update(",
    "delete(",
    ".add(",
    ".add_all(",
    "flush()",
    "commit()",
    "rollback()",
    "upsert",
    "merge(",
)

_SOURCE = Path(inspect.getfile(repository_module)).read_text()


def _repository_methods():
    tree = ast.parse(_SOURCE)
    lines = _SOURCE.split("\n")
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PersonalLearningRepository"
    )
    for fn in cls.body:
        if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if fn.name in ("_use_session", "_read_session", "unit_of_work"):
            continue
        yield fn.name, "\n".join(lines[fn.lineno - 1 : fn.end_lineno])


class TestSessionChoiceMatchesWhatTheMethodDoes:
    """The invariant a mechanical conversion could break silently."""

    def test_no_method_writes_through_the_read_path(self):
        offenders = [
            name
            for name, body in _repository_methods()
            if "self._read_session(" in body and any(m in body for m in WRITE_MARKERS)
        ]
        assert offenders == [], (
            "These write through `_read_session`, which opens no transaction, so "
            f"they would escape `unit_of_work`: {offenders}"
        )

    def test_every_read_only_method_uses_the_read_path(self):
        """The converse, so the saving does not quietly regress.

        A read-only method left on `_use_session` still works; it just pays for a
        transaction it does not use, which is the defect being fixed. New read
        methods land on the wrong helper by default, since `_use_session` is the
        one every existing example shows.
        """
        stragglers = [
            name
            for name, body in _repository_methods()
            if "self._use_session(" in body and not any(m in body for m in WRITE_MARKERS)
        ]
        assert stragglers == [], (
            "These only read but still open and commit a transaction — switch them "
            f"to `_read_session`: {stragglers}"
        )

    def test_the_split_is_actually_used_on_both_sides(self):
        """Guards against the whole thing being reverted to one helper."""
        bodies = list(_repository_methods())
        reads = [n for n, b in bodies if "self._read_session(" in b]
        writes = [n for n, b in bodies if "self._use_session(" in b]
        assert len(reads) > 40
        assert len(writes) > 20


class TestReadSessionRespectsACallerTransaction:
    @pytest.mark.asyncio
    async def test_a_supplied_session_is_yielded_untouched(self):
        """No connection is acquired and no execution option is set.

        The failure this prevents: reconfiguring isolation on a session that is
        already inside a transaction, which raises — and would do so only for the
        callers that pass a session, i.e. only inside `unit_of_work`.
        """
        repo = PersonalLearningRepository()

        class Sentinel:
            def __init__(self):
                self.connection_calls = 0

            async def connection(self, **kwargs):
                self.connection_calls += 1

        sentinel = Sentinel()
        async with repo._read_session(sentinel) as yielded:
            assert yielded is sentinel
        assert sentinel.connection_calls == 0

    @pytest.mark.asyncio
    async def test_the_write_path_also_yields_a_supplied_session_untouched(self):
        """And in particular does not commit it — the caller owns that."""
        repo = PersonalLearningRepository()

        class Sentinel:
            def __init__(self):
                self.commits = 0
                self.rollbacks = 0

            async def commit(self):
                self.commits += 1

            async def rollback(self):
                self.rollbacks += 1

        sentinel = Sentinel()
        async with repo._use_session(sentinel) as yielded:
            assert yielded is sentinel
        assert (sentinel.commits, sentinel.rollbacks) == (0, 0)


class TestReadSessionOpensNoTransaction:
    @pytest.mark.asyncio
    async def test_autocommit_is_requested_before_the_first_statement(self, monkeypatch):
        """Order matters: the option has to be set on connection acquisition.

        Set after the first `execute`, the implicit BEGIN has already happened and
        the round trip it costs is already spent.
        """
        repo = PersonalLearningRepository()
        events: list[str] = []

        class FakeSession:
            async def connection(self, *, execution_options=None):
                events.append(f"connection:{(execution_options or {}).get('isolation_level')}")

            async def execute(self, _stmt):
                events.append("execute")

            async def commit(self):
                events.append("commit")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                events.append("close")
                return False

        def factory():
            return FakeSession()

        monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

        stmt = select(func.count()).select_from(PrepTopic)
        async with repo._read_session(None) as session:
            await session.execute(stmt)

        assert events == ["connection:AUTOCOMMIT", "execute", "close"]
        # The point of the whole change.
        assert "commit" not in events

    @pytest.mark.asyncio
    async def test_the_write_path_still_commits(self, monkeypatch):
        repo = PersonalLearningRepository()
        events: list[str] = []

        class FakeSession:
            async def execute(self, _stmt):
                events.append("execute")

            async def commit(self):
                events.append("commit")

            async def rollback(self):
                events.append("rollback")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(
            repository_module, "get_session_factory", lambda: (lambda: FakeSession())
        )

        async with repo._use_session(None) as session:
            await session.execute(None)

        assert events == ["execute", "commit"]

    @pytest.mark.asyncio
    async def test_the_write_path_rolls_back_on_failure(self, monkeypatch):
        repo = PersonalLearningRepository()
        events: list[str] = []

        class FakeSession:
            async def commit(self):
                events.append("commit")

            async def rollback(self):
                events.append("rollback")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(
            repository_module, "get_session_factory", lambda: (lambda: FakeSession())
        )

        with pytest.raises(RuntimeError):
            async with repo._use_session(None):
                raise RuntimeError("query blew up")

        assert events == ["rollback"]
