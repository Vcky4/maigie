"""The plan-shapes route, and the class of bug that made it a permanent `500`.

`GET /learning/study-plans/shapes` answered `500` on every call from the day it was
written. The route body is two lines::

    from ..plan_shapes import PLAN_SHAPES
    return PLAN_SHAPES

and the import was one level too high: `routes.py` already lives in
`src.domains.personal_learning`, so `..plan_shapes` resolved to `src.domains.plan_shapes`,
which does not exist. The level was almost certainly copied from
`services/study_plan_service.py`, where `..plan_shapes` *is* correct because the service
sits one package deeper.

Three things conspired to hide it, and each is worth naming because they are what these
tests are shaped around:

1. **The import is inside the function.** Nothing resolved it at startup, so the app
   booted clean and the module graph looked fine.
2. **`test_study_plan_rhythm.py` covers the catalogue thoroughly** — ids unique, phases
   ordered, `find_shape` tolerant of nonsense — by importing `plan_shapes` *directly*.
   The module was never the broken thing. Nothing exercised the route that reads it.
3. **Both clients degrade quietly.** The wizard's step 1 falls back to "You can still
   continue — Maigie will choose the phases", which is a reasonable-looking screen. A
   learner never saw an error, and a developer saw a shape picker that seemed to be
   waiting on something.

So the first test calls the endpoint function itself. No database, no auth, no HTTP: the
route is unauthenticated and touches nothing, and the whole defect was a name that could
not be resolved. A test that needed Postgres would *skip* on a machine without it, which
for this bug is indistinguishable from passing.

The second test generalises it. The same mistake existed in `knowledge/routes.py`, on the
topic-completion event emit, where it made any topic update carrying `completed` a `500`
— and it was found by sweeping for the pattern rather than by anyone reporting it. So the
sweep is the test.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.domains.personal_learning import plan_shapes


@pytest.mark.asyncio
async def test_shapes_route_resolves_its_import_and_returns_the_catalogue():
    """The route runs, and answers with the catalogue rather than a copy of it.

    Calling the endpoint function directly is the point: the failure was a
    `ModuleNotFoundError` raised the moment the body executed, so anything that reaches the
    body catches it. Asserting identity with `PLAN_SHAPES` also pins that the route serves
    the shared catalogue — the one `generate_plan` hands to the model — rather than a second
    list that could drift from it, which is the whole reason this moved server-side.
    """
    from src.domains.personal_learning.routes import list_plan_shapes

    shapes = await list_plan_shapes()

    assert shapes is plan_shapes.PLAN_SHAPES
    assert len(shapes) == 4, "the wizard offers four shapes"
    # Enough of the response shape to know a client can render step 1 from it.
    for shape in shapes:
        assert {
            "id",
            "title",
            "category",
            "description",
            "defaultTitle",
            "defaultOutcome",
            "phases",
        } <= shape.keys()
        assert shape["phases"], f"{shape['id']} has no phases to preview"


def test_every_relative_import_in_the_codebase_resolves():
    """No module is imported from a package that does not exist.

    A guard against the class rather than the instance. Two independent occurrences of the
    same off-by-one-level import were live at once, both inside function bodies, both
    invisible until the line ran — one on plan shapes, one on topic completion. Neither
    would have been caught by importing the app, and a linter does not resolve relative
    levels against the filesystem.

    Resolution mirrors Python's own: `level` 1 means the containing package, and each extra
    dot climbs one further. A target counts as resolved if it exists as a package directory
    or as a module file; `from .x import y` where `y` is a name re-exported by `x` is
    therefore checked at the module level, which is the level the bug lived at.

    Files carrying a UTF-8 BOM are read with `utf-8-sig` rather than skipped — thirteen
    modules in this tree have one, and skipping them would quietly exempt them.
    """
    source_root = pathlib.Path(__file__).resolve().parents[1] / "src"
    assert source_root.is_dir(), "expected to find the source tree next to tests/"

    unresolved: list[str] = []
    checked = 0

    for path in sorted(source_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as error:  # pragma: no cover - a syntax error is its own failure
            unresolved.append(f"{path}: could not parse ({error})")
            continue
        checked += 1

        # `a/b/c.py` and `a/b/__init__.py` are both in package `a/b`.
        package = list(path.with_suffix("").parts)[:-1]

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue

            climb = node.level - 1
            if climb > len(package):
                unresolved.append(
                    f"{path}:{node.lineno} climbs above the source root: "
                    f"from {'.' * node.level}{node.module or ''} import ..."
                )
                continue

            base = package[: len(package) - climb]
            target = pathlib.Path(*base, *(node.module.split(".") if node.module else []))
            if target.is_dir() or target.with_suffix(".py").exists():
                continue

            unresolved.append(
                f"{path}:{node.lineno} from {'.' * node.level}{node.module or ''} import ... "
                f"→ no module or package at {target}"
            )

    assert checked > 200, f"only parsed {checked} modules — the sweep is not seeing the tree"
    assert not unresolved, "unresolved relative imports:\n" + "\n".join(unresolved)
