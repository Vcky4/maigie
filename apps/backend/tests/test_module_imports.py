"""Every module under ``src`` must be importable.

This exists because a domain reorganisation left eleven modules importing paths that
no longer existed: ``src.utils.exceptions``, ``src.models.notes``,
``src.domains.billing.config``, and others. Nothing failed at startup because the
broken imports were either module-level in modules only reached lazily, or nested
inside request handlers. The result was that the notes endpoints and every payment
webhook raised ``ModuleNotFoundError`` the first time a real user exercised them,
while the test suite stayed green.

Importing a module proves only that its imports resolve, which is exactly the class
of breakage that slipped through. It is a cheap invariant and worth asserting for
the whole tree rather than per module.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import importlib  # noqa: E402
import pkgutil  # noqa: E402

import pytest  # noqa: E402

import src  # noqa: E402


def _module_names() -> list[str]:
    return sorted(mod.name for mod in pkgutil.walk_packages(src.__path__, prefix="src."))


@pytest.mark.parametrize("module_name", _module_names())
def test_module_is_importable(module_name: str) -> None:
    importlib.import_module(module_name)
