"""Guard against reading a camelCase database column name off a SQLAlchemy model.

Every model in this codebase maps a snake_case Python attribute onto a camelCase
column, e.g. ``payment_provider: Mapped[str | None] = mapped_column("paymentProvider", ...)``.
Reading ``user.paymentProvider`` therefore raises ``AttributeError`` at runtime,
which surfaces as a 500 rather than a test failure, because the name only exists
in the database.

This test collects the column names that are a column on some model and an
attribute on no model, then fails if any of them is read off an object in ``src``.
"""

import pathlib
import re

import src.app  # noqa: F401  (imports every domain so all mappers are configured)
from src.shared.database.base import Base

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# Pydantic contracts legitimately use camelCase field names for the wire format.
SKIP_FILENAMES = {"models.py"}

# Variables that are never a mapped ORM instance: Pydantic bodies, the models
# module itself, SQLAlchemy Column objects, settings, dicts.
NON_ORM_VARS = {
    "models",
    "c",
    "body",
    "data",
    "payload",
    "request",
    "req",
    "settings",
    "self",
}

# Modules that still speak the removed Prisma client and fail via the
# PrismaClientRemoved sentinel before any of this could matter. They are dead
# code kept for the port, not live paths. Remove entries as they are ported.
UNMIGRATED_MODULES = {
    "domains/billing/services/paystack_service.py",
    "domains/billing/services/referral_rewards_service.py",
}

# Rows returned from raw ``text()`` SQL, where the camelCase column name is the
# correct key. Keyed by "relative/path.py:line-ish" variable name.
RAW_SQL_ROW_VARS = {
    "domains/learning_spaces/services/space_impl.py": {"note", "original"},
}


def _column_only_names() -> set[str]:
    mapped_attrs: set[str] = set()
    column_names: set[str] = set()
    for mapper in Base.registry.mappers:
        mapped_attrs |= {attr.key for attr in mapper.column_attrs}
        column_names |= {c.name for c in mapper.columns if re.search(r"[a-z][A-Z]", c.name)}
    return column_names - mapped_attrs


def _code_lines(text: str):
    """Yield (line_number, stripped_line) skipping comments and docstring bodies."""
    lines = text.splitlines()
    consumed = 0
    for number, line in enumerate(lines, 1):
        inside_docstring = text[:consumed].count('"""') % 2 == 1
        consumed += len(line) + 1
        stripped = line.strip()
        if inside_docstring or stripped.startswith("#"):
            continue
        yield number, stripped


def test_no_source_file_reads_a_column_only_attribute():
    column_only = _column_only_names()
    assert column_only, "expected some camelCase columns without a matching attribute"

    pattern = re.compile(
        r"(?<![\w\"'`])(\w+)\.(" + "|".join(map(re.escape, sorted(column_only))) + r")\b"
    )

    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if path.name in SKIP_FILENAMES or relative in UNMIGRATED_MODULES:
            continue
        allowed_vars = NON_ORM_VARS | RAW_SQL_ROW_VARS.get(relative, set())
        text = path.read_text()
        for number, stripped in _code_lines(text):
            for match in pattern.finditer(stripped):
                if match.group(1) in allowed_vars:
                    continue
                offenders.append(f"src/{relative}:{number}  {match.group(0)}  |  {stripped[:100]}")

    assert not offenders, (
        "These read a database column name off a model, which raises AttributeError "
        "at runtime. Use the snake_case mapped attribute instead:\n" + "\n".join(offenders)
    )
