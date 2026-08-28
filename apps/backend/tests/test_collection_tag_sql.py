"""Guards the JSON-vs-array mismatch that broke collection auto-seeding.

`SavedResource.tags` is a **`json`** column. Two raw-SQL queries treated it as a Postgres array:
`find_cross_type_tags` used `unnest(sr.tags)` and `_find_items_for_tag` used `:tag = ANY(sr.tags)`.
Both raise `UndefinedFunctionError` on Postgres, and both are reached through
`auto_seed_collections`, which wraps everything in a logging `except` — so collection auto-seeding
raised on **every learning-dashboard load** since the SQLAlchemy migration and never once produced a
collection, while reporting success to the caller.

The operators are Prisma leftovers: `tags String[]` there was a real `text[]`. The migration
redeclared the column `JSON` and this raw SQL was never revisited.

**What this file can and cannot prove.** It cannot execute the queries: the suite runs SQLite, which
has neither `unnest` nor `json_array_elements_text`, and that absence is precisely why the bug was
invisible to 2000+ passing tests. So this asserts the *shape* of the SQL against the *declared
column type* — enough to fail if anyone reaches for an array operator on a JSON column again, which
is the mistake that was actually made. The queries themselves were verified by running them against
the real Postgres database, including rows holding a non-array and a NULL.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from sqlalchemy import JSON

#: Postgres operators and functions that require a real array. Applying any of them to a `json`
#: column raises at execution time rather than at import or query-build time, so nothing catches
#: them until a request runs.
_ARRAY_ONLY_SQL = ("unnest(", "= any(", "&& ", "@> array", "array_agg(sr.tags")


def _sql_of(func) -> str:
    """Every SQL string literal in the function, lowercased and concatenated.

    Extracted through the AST rather than by searching the raw source, because prose in this
    codebase quotes the operators it replaced: both fixes are documented in place by naming
    `unnest(` and `= ANY(`, so a substring search over the source finds them in the very
    docstring and comments explaining why they are gone. Only string literals that look like a
    query are considered, which excludes the docstring.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "select" in node.value.lower()
    ]
    assert statements, "no SQL literal found; the extraction, not the query, is probably broken"
    return "\n".join(statements).lower()


def test_saved_resource_tags_is_a_json_column_not_an_array():
    """The premise the two queries have to respect.

    If this ever becomes a Postgres `ARRAY`, the array operators become correct and the two
    assertions below should be revisited rather than worked around.
    """
    from src.domains.personal_learning.db_models import SavedResource

    column = SavedResource.__table__.c.tags
    assert isinstance(
        column.type, JSON
    ), f"tags is {column.type!r}; the tag queries are written for a JSON column"
    assert column.nullable is True


def test_cross_type_tag_query_expands_json_not_an_array():
    from src.domains.personal_learning.repository import PersonalLearningRepository

    source = _sql_of(PersonalLearningRepository.find_cross_type_tags)

    assert "json_array_elements_text" in source
    for operator in _ARRAY_ONLY_SQL:
        assert operator not in source, f"{operator!r} needs a real array; tags is json"


def test_items_for_tag_query_matches_json_not_an_array():
    from src.domains.personal_learning.services.collection_service import _find_items_for_tag

    source = _sql_of(_find_items_for_tag)

    assert "json_array_elements_text" in source
    for operator in _ARRAY_ONLY_SQL:
        assert operator not in source, f"{operator!r} needs a real array; tags is json"


@pytest.mark.parametrize(
    "func_path",
    [
        (
            "src.domains.personal_learning.repository",
            "PersonalLearningRepository.find_cross_type_tags",
        ),
        ("src.domains.personal_learning.services.collection_service", "_find_items_for_tag"),
    ],
)
def test_json_expansion_is_guarded_against_non_array_values(func_path):
    """`json_array_elements_text` raises on anything that is not a JSON array.

    The column is nullable and typed `dict | None` on the ORM, so a non-array is representable and
    a single bad row would otherwise take out the whole query. The guard has to sit **inside** the
    function argument rather than in a `WHERE`, because a `WHERE` at the same query level is not
    guaranteed to be evaluated before a set-returning function.
    """
    import importlib

    module_name, attr = func_path
    module = importlib.import_module(module_name)
    target = module
    for part in attr.split("."):
        target = getattr(target, part)

    source = _sql_of(target)
    assert "json_typeof" in source, "expansion is unguarded; one non-array row breaks the query"
    assert "'[]'::json" in source


def test_auto_seeding_still_swallows_failures_but_logs_them():
    """The `except` is deliberate — seeding is a side errand on a dashboard read and must not fail
    it — but it is also what hid this for so long. Pinned so nobody removes the logging and leaves
    a bare `pass`, which would make the next occurrence of this silent as well as broken.
    """
    from src.domains.personal_learning.services import collection_service

    # Raw source, not `_sql_of`: this asserts on control flow, and the function contains no SQL.
    source = inspect.getsource(collection_service.auto_seed_collections).lower()
    assert "except exception" in source
    assert "logger.warning" in source
    assert "exc_info=true" in source
