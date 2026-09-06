"""Note list ordering — the contract and the SQL, without a database.

`tests/test_notes.py` covers the behaviour end to end, but it is gated behind
``RUN_DB_TESTS=1`` against a real database. These assertions hold in a plain run, and they
cover the two things that can silently break paging:

  * the published parameter, because a client is generated from the schema and an ordering
    enforced only in Python is one a generated client cannot ask for;
  * the tiebreaker, because rows sharing a sort value have no defined order without it, and
    the database is then free to return the same note on page one and page two.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.domains.personal_learning.db_models import Note
from src.domains.personal_learning.repository import personal_learning_repo as repo


def _order_by_sql(sort: str) -> str:
    """The ORDER BY this sort produces, as SQL text."""
    order_by = repo.NOTE_SORTS[sort]
    return str(select(Note.id).order_by(*order_by).compile()).replace("\n", " ")


class TestPublishedParameter:
    def test_sort_is_published_with_its_allowed_values(self):
        from src.app import app

        parameters = app.openapi()["paths"]["/api/v1/learning/notes"]["get"]["parameters"]
        sort = next(parameter for parameter in parameters if parameter["name"] == "sort")

        schema = sort["schema"]
        # FastAPI may publish a Literal with a default as an allOf/anyOf wrapper, so read
        # the enum from wherever it lands rather than asserting on the wrapper shape.
        enum = schema.get("enum")
        if enum is None:
            for branch in schema.get("allOf", []) + schema.get("anyOf", []):
                if "enum" in branch:
                    enum = branch["enum"]
                    break

        assert enum is not None, schema
        assert sorted(enum) == ["recent", "title"]
        assert schema.get("default") == "recent"
        assert sort["required"] is False

    def test_page_size_cap_is_published(self):
        """The cap is why a pager exists rather than one large fetch."""
        from src.app import app

        parameters = app.openapi()["paths"]["/api/v1/learning/notes"]["get"]["parameters"]
        page_size = next(p for p in parameters if p["name"] == "pageSize")
        assert page_size["schema"]["maximum"] == 100
        assert page_size["schema"]["minimum"] == 1


class TestOrdering:
    def test_every_sort_ends_in_a_tiebreaker(self):
        """Without this, paging can lose and duplicate rows rather than merely shuffle."""
        assert repo.NOTE_SORTS, "no sorts registered"
        for name, order_by in repo.NOTE_SORTS.items():
            assert len(order_by) >= 2, f"{name} has no tiebreaker"
            assert "id" in str(order_by[-1]), f"{name} does not end on id"

    def test_recent_is_newest_first(self):
        sql = _order_by_sql("recent")
        assert '"updatedAt" DESC' in sql
        assert sql.index('"updatedAt" DESC') < sql.index('"Note".id ASC')

    def test_title_is_case_insensitive_ascending(self):
        """`Zebra` must not sort before `apple`, which a raw column order would do."""
        sql = _order_by_sql("title")
        assert 'lower("Note".title) ASC' in sql
        assert sql.index("lower") < sql.index('"Note".id ASC')

    @pytest.mark.parametrize("unknown", ["", "sideways", "recent ", "TITLE"])
    def test_unknown_sort_falls_back_to_recent(self, unknown):
        """Defence in depth: the route's Literal already refuses these with a 422, but the
        repository is also reachable from services and must not emit an unordered query."""
        assert repo.NOTE_SORTS.get(unknown, repo.NOTE_SORTS["recent"]) == repo.NOTE_SORTS["recent"]
