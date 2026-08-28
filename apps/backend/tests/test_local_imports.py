"""Every `src.*` import written inside a function body must resolve.

`test_module_imports.py` only proves that *module-level* imports work. Imports nested in a
function are invisible to it, and the codebase uses them heavily and legitimately, to break
cycles and to keep worker start-up cheap.

That blind spot hid real breakage. Three Celery tasks imported from `src.tasks.*`, a package
that held nothing but `__pycache__`, and each import sat inside the task body. The worker
module imported cleanly, the test suite stayed green, and the task failed only when the beat
schedule fired it. The same pattern hid `reset_credits_for_period_start` being imported from
the wrong module: the name had moved, so it was the *symbol* that was missing rather than the
module.

So this checks both halves: the target module imports, and it actually provides the names
being asked of it.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import ast  # noqa: E402
import importlib  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"

#: Files the scan could not parse, and therefore could not check. Asserted empty below.
_UNPARSEABLE: list[str] = []


def _function_local_imports() -> list[tuple[str, str, tuple[str, ...]]]:
    """Return (source_file, target_module, imported_names) for imports inside functions."""
    found: list[tuple[str, str, tuple[str, ...]]] = []

    for path in sorted(SRC.rglob("*.py")):
        # `utf-8-sig`, not `utf-8`. **A byte-order mark used to switch this whole checker off, one file at a
        # time.** Read as plain UTF-8, a leading BOM survives as a `\ufeff` character, `ast.parse` rejects it
        # as an invalid non-printable, and the `except SyntaxError: continue` below skipped the file in
        # silence. Four files carried one — two `classrooms` services, `skill_registry` and
        # `planning_service` — and between them they hid **five function-local imports of names that do not
        # exist**, from July until the BOMs happened to be stripped by an unrelated formatting pass. The
        # checker was green the entire time. `utf-8-sig` strips a BOM if present and is a no-op otherwise.
        source = path.read_bytes().decode("utf-8-sig", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            # Recorded rather than skipped. A file this cannot parse is a file this cannot check, and
            # silently passing on it is how the above went unnoticed for two months.
            _UNPARSEABLE.append(path.relative_to(SRC.parent).as_posix())
            continue

        relative = path.relative_to(SRC.parent).as_posix()

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            # Only descend into this function's own body, but nested functions are
            # reached because ast.walk visits them as their own FunctionDef too.
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom):
                    # Relative imports inside functions are resolved against the package
                    # and are rare; skip them rather than guess the anchor.
                    if inner.level or not inner.module:
                        continue
                    if inner.module.startswith("src."):
                        found.append((relative, inner.module, tuple(a.name for a in inner.names)))
                elif isinstance(inner, ast.Import):
                    for alias in inner.names:
                        if alias.name.startswith("src."):
                            found.append((relative, alias.name, ()))

    # Collapse duplicates so the same import in two functions is checked once.
    return sorted(set(found))


_CASES = _function_local_imports()


def test_the_scan_found_something():
    """Guards against the scan silently matching nothing and passing vacuously."""
    assert len(_CASES) > 20, f"only found {len(_CASES)} function-local src imports"


def test_every_source_file_was_readable():
    """A file this cannot parse is a file this cannot check.

    The guard that was missing. Four files carried a UTF-8 BOM, which made `ast.parse` reject them, and the
    scan skipped them without a word — hiding five broken imports for two months while reporting success.
    Coverage that can silently shrink is not coverage, so an unparseable file now fails here by name instead
    of quietly leaving the checked set.
    """
    assert not _UNPARSEABLE, (
        f"{len(_UNPARSEABLE)} file(s) could not be parsed and so were not checked: {_UNPARSEABLE}"
    )


def _provides(module: object, target_module: str, name: str) -> bool:
    """Whether `from target_module import name` would succeed.

    Two ways it can: `name` is bound on the module, or `name` is a **submodule** of it — which is legal and
    which `hasattr` cannot see until something has imported it.

    The submodule probe has to be guarded, and that guard is the bug this replaced. `find_spec` on
    `"a.b.c"` imports `a.b` and then reads its `__path__` to look for `c`; when `a.b` is a plain module
    rather than a package it has no `__path__`, and `find_spec` **raises** instead of returning `None`. So
    the six genuine failures in this suite arrived as an unreadable `ModuleNotFoundError: __path__ attribute
    not found` from inside `importlib`, three frames deep, rather than as this test's own message naming the
    file and the missing symbol. A checker whose failure output hides what failed is a checker people learn
    to ignore.
    """
    if hasattr(module, name):
        return True
    # A module is not a package, so it has no submodules and there is nothing further to probe.
    if not hasattr(module, "__path__"):
        return False
    try:
        return importlib.util.find_spec(f"{target_module}.{name}") is not None
    except (AttributeError, ImportError, ValueError):
        # `find_spec` raises rather than returning None for several shapes of missing target. Every one of
        # them means the name is not importable, which is the only thing this function claims.
        return False


#: Imports that are known broken, in code nothing can reach.
#:
#: **Every entry here is a real defect** — the name does not exist and the import would raise. They are
#: registered rather than fixed because each sits in a facade written during a migration that never finished,
#: and none of it is reachable:
#:
#:  - the `classrooms` router is **commented out** in `src/app.py`, so no request reaches those services;
#:  - `get_skill_registry`, `execute_skill` and `planning_service.generate_study_plan` have **no callers
#:    anywhere** in `src` or `tests`.
#:
#: They were invisible until now for a reason worth recording: all four files carried a UTF-8 BOM, which made
#: `ast.parse` reject them and the scan skip them in silence. See `_function_local_imports`.
#:
#: Not fixed here, deliberately. Two of the five have no target to point at — `_require_role` does not exist
#: anywhere in `src`, so "fixing" it means **writing an authorization check** for routes that are not mounted,
#: and `planning_impl` has `create_study_plan(user_id, goal, …)` which is a different function from the
#: `generate_study_plan(user_id, course_id)` the facade wants, not a rename. Inventing either to turn a test
#: green would be worse than recording the truth. The honest fix is to delete the dead facades or finish the
#: migration, and that is a decision for whoever owns those domains.
#:
#: **Strict xfail, so this register cannot rot.** The moment one of these resolves — because the code was
#: fixed, or the facade deleted — the entry becomes an unexpected pass and this file fails until it is
#: removed. A plain skip-list would quietly outlive the problem it describes.
_KNOWN_BROKEN: dict[tuple[str, str], str] = {
    (
        "src/domains/classrooms/services/classroom_service.py",
        "src.domains.learning_spaces.services.space_impl",
    ): "`_require_role` exists nowhere in src; the classrooms router is not mounted",
    (
        "src/domains/classrooms/services/session_service.py",
        "src.domains.learning_spaces.services.space_impl",
    ): "`_require_role` exists nowhere; `suggest_sessions` is `suggest_group_sessions`",
    (
        "src/domains/intelligence/action/skill_registry.py",
        "src.domains.intelligence.action.skills.handlers",
    ): "`handle_skill_call` is `handle_tool_call`, with a different signature; no callers",
    (
        "src/domains/intelligence/action/skill_registry.py",
        "src.domains.intelligence.action.skills.registry",
    ): "`get_registry` does not exist; the module exposes a `skill_registry` instance. No callers",
    (
        "src/domains/intelligence/planning/planning_service.py",
        "src.domains.intelligence.planning.planning_impl",
    ): "`generate_study_plan` does not exist; `create_study_plan` takes a goal, not a course. No callers",
}


def test_the_known_broken_register_is_current():
    """Every registered entry must still correspond to a real import the scan found.

    Stops the register describing code that has since been deleted or moved, which would leave a stale excuse
    sitting in the suite looking like a live exemption.
    """
    scanned = {(src, mod) for src, mod, _ in _CASES}
    stale = sorted(key for key in _KNOWN_BROKEN if key not in scanned)
    assert not stale, f"_KNOWN_BROKEN names imports that no longer exist; remove them: {stale}"


@pytest.mark.parametrize(
    "source_file,target_module,names",
    _CASES,
    ids=[f"{src}::{mod}" for src, mod, _ in _CASES],
)
def test_function_local_import_resolves(
    source_file: str, target_module: str, names: tuple[str, ...], request: pytest.FixtureRequest
) -> None:
    reason = _KNOWN_BROKEN.get((source_file, target_module))
    if reason is not None:
        request.applymarker(pytest.mark.xfail(strict=True, reason=reason))
    try:
        module = importlib.import_module(target_module)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"{source_file} imports {target_module!r} inside a function, "
            f"but that module does not exist: {exc}"
        )

    missing = [name for name in names if not _provides(module, target_module, name)]
    assert not missing, (
        f"{source_file} imports {missing} from {target_module!r} inside a function, "
        "but those names are not there"
    )
