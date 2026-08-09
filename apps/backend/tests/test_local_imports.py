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


def _function_local_imports() -> list[tuple[str, str, tuple[str, ...]]]:
    """Return (source_file, target_module, imported_names) for imports inside functions."""
    found: list[tuple[str, str, tuple[str, ...]]] = []

    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - would fail the import test too
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


@pytest.mark.parametrize(
    "source_file,target_module,names",
    _CASES,
    ids=[f"{src}::{mod}" for src, mod, _ in _CASES],
)
def test_function_local_import_resolves(
    source_file: str, target_module: str, names: tuple[str, ...]
) -> None:
    try:
        module = importlib.import_module(target_module)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"{source_file} imports {target_module!r} inside a function, "
            f"but that module does not exist: {exc}"
        )

    missing = [
        name
        for name in names
        if not hasattr(module, name)
        # `from pkg import submodule` is valid even when the attribute is not yet bound.
        and importlib.util.find_spec(f"{target_module}.{name}") is None
    ]
    assert not missing, (
        f"{source_file} imports {missing} from {target_module!r} inside a function, "
        "but those names are not there"
    )
