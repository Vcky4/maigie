"""Search-term handling for `LIKE`/`ILIKE` predicates.

One place, because the alternative had already gone wrong twice. `_escape_like` existed as a
private static method on `KnowledgeRepository` and was called from exactly one of the twelve
`ilike` sites in the backend; every other search — notes, documents, flashcards, saved resources,
study plans, exam preps, the admin user list — interpolated the learner's term straight into a
pattern. So `100%` matched every row and `a_b` matched `axb`.

That failure mode is worth naming: the query **succeeds and returns the wrong rows**. Nothing
errors, nothing is logged, and the result is a plausible-looking page, so it survives review and
manual testing alike. It is the same shape as the other search defect found in this area, where a
`where["OR"]` clause was built and never read.
"""

from __future__ import annotations

from typing import Any

#: The escape character used with every `ilike(..., escape=...)` call built here. Backslash is
#: conventional, and passing it explicitly is required — Postgres defaults to it but SQLite does
#: not, and the test suite runs on SQLite.
LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Escape the wildcards `LIKE` treats as syntax.

    `%` matches any run of characters and `_` matches exactly one, so an unescaped term containing
    either silently widens the search instead of narrowing it. The backslash is escaped first, or
    it would double-escape the escapes added after it.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains_pattern(term: str) -> str:
    """A `%term%` pattern with the term escaped.

    Use with ``escape=LIKE_ESCAPE``; :func:`ilike_any` does that for you.
    """
    return f"%{escape_like(term)}%"


def ilike_any(term: str, *columns: Any) -> Any:
    """An `OR` of case-insensitive contains-matches across `columns`.

    Wraps the three things every call site has to get right — escaping the term, passing the
    escape character, and combining the columns — so that getting one of them wrong is not
    possible one column at a time. A single column is returned bare rather than wrapped in a
    one-element `or_`, which SQLAlchemy accepts but which reads oddly in logged SQL.
    """
    from sqlalchemy import or_

    pattern = contains_pattern(term)
    clauses = [column.ilike(pattern, escape=LIKE_ESCAPE) for column in columns]
    if not clauses:
        raise ValueError("ilike_any needs at least one column")
    return clauses[0] if len(clauses) == 1 else or_(*clauses)
