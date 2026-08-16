"""Translating wire field names to ORM attribute names, without losing anything on the way.

Every repository in this codebase maps a request dictionary onto ORM attributes through a
`field_map`, and every one of them was written like this:

    return {field_map[k]: v for k, v in data.items() if k in field_map}

The `if k in field_map` is the problem. A key the map does not know is **silently discarded**: the
request succeeds, the response looks right because it is built from the request rather than from the
row, and the value never reaches the database. Nothing fails, nothing logs, and the defect is only
found when somebody notices a saved value that is not saved.

This is not hypothetical. Three instances were found in one afternoon:

- `search` on the course list endpoint was accepted and dropped, so searching returned everything.
- `category`, `tags`, `outcomes`, `instructorName` and `instructorRole` were added to `CourseCreate`
  and reached a service allowlist that did not know them, so five fields on a create form did nothing.
- The three flashcard review aids reached `_map_flashcard`, which did not list them.

The pattern is worse than a plain bug because it *scales with the schema*: every field added to a
request model is a new opportunity to lose data, and the failure mode is silence.

## What this module does instead

`map_fields` raises `UnmappedFieldError` on a key the map does not cover. A field that has not been
wired up is then a loud failure in development and in tests, rather than a quiet one in production.

This is deliberately strict rather than permissive-with-a-warning. A warning in a log nobody reads is
the same as silence, and the cost of strictness is paid once, by the developer who added the field,
at the moment they can still fix it cheaply.

## Why not just pass the dictionary through

Handing the caller's dictionary straight to the ORM constructor would fail loudly too, but on the
wrong thing: it would let a client set `progress`, `userId` or SM-2 scheduling state directly by
naming them in a request body. The map is also an allowlist, and that job matters. The fix is to keep
the allowlist and make *omissions from it* loud, not to remove it.
"""

from typing import Any


class UnmappedFieldError(KeyError):
    """A request field reached a mapper that does not know how to store it.

    Raised rather than ignored because ignoring it loses the learner's data. If you are reading this
    from a stack trace, the fix is almost always one line: add the field to the `field_map` of the
    mapper named in the message, pointing at the ORM attribute it belongs in.

    If the field genuinely should not be persisted — a transient flag, something handled elsewhere in
    the service — add it to `ignore` at the call site with a comment saying why. That keeps the
    decision visible instead of implicit.
    """

    def __init__(self, entity: str, unknown: list[str], known: list[str]) -> None:
        self.entity = entity
        self.unknown = unknown
        super().__init__(
            f"{entity}: no mapping for {sorted(unknown)}. "
            f"These fields would have been silently discarded. "
            f"Add them to the field map (known fields: {sorted(known)}), "
            f"or pass them in `ignore` if they are deliberately not persisted."
        )


def map_fields(
    data: dict[str, Any],
    field_map: dict[str, str],
    *,
    entity: str,
    ignore: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Translate wire names to attribute names, refusing to drop anything quietly.

    Args:
        data: The incoming values, keyed by wire name.
        field_map: Wire name to ORM attribute name. Also the allowlist of what may be written.
        entity: What is being mapped, for the error message — the caller knows, the helper does not.
        ignore: Wire names that are deliberately not persisted by this mapper. Every entry should
            carry a comment at the call site explaining where the field is handled instead.

    Returns:
        The values keyed by ORM attribute name.

    Raises:
        UnmappedFieldError: If `data` holds a key that is neither mapped nor explicitly ignored.

    Keys are absent from the result only when they were absent from `data`, so a caller using
    `exclude_unset=True` keeps the distinction between "not sent" and "sent as null" — which is what
    makes clearing a field expressible at all.
    """
    unknown = [key for key in data if key not in field_map and key not in ignore]
    if unknown:
        raise UnmappedFieldError(entity, unknown, list(field_map))

    return {field_map[key]: value for key, value in data.items() if key in field_map}


def reject_unclearable(
    data: dict[str, Any],
    model: type,
    *,
    field_map: dict[str, str] | None = None,
) -> None:
    """Refuse an explicit null for a column that cannot hold one.

    The companion to the other half of this defect class. Several services filtered nulls out of an
    update — `{k: v for k, v in data.items() if v is not None}` — while their route dumped the body
    with `exclude_unset=True`. The combination made **clearing any field impossible while reporting
    success**: sending `{"category": null}` returned `200` and changed nothing, because a key only
    arrives when the client sent it and the filter then removed exactly the ones sent as null.

    Dropping the filter restores clearing, but exposes the case it was accidentally covering: a null
    aimed at a `NOT NULL` column. Left alone that surfaces as an integrity error naming a database
    constraint, which the client cannot act on.

    Nullability is read from the mapped columns rather than listed by hand, so the rule cannot drift
    from the schema. A column made non-nullable in a later migration starts being enforced here without
    anyone remembering to update a list.

    Args:
        data: Incoming values keyed by wire name.
        model: The SQLAlchemy model the update targets.
        field_map: Wire name to attribute name, when they differ. Unmapped keys are looked up by their
            own name, which covers the common case where the two match.

    Raises:
        ValueError: If any key holds an explicit null for a non-nullable column. The caller converts
            this into its domain's validation error, so the message reaches the client as a 400.
    """
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(model)
    field_map = field_map or {}

    unclearable = []
    for wire_name, value in data.items():
        if value is not None:
            continue
        attr_name = field_map.get(wire_name, wire_name)
        attr = mapper.attrs.get(attr_name)
        # Not a mapped column — a relationship, or a field this model does not own. Whether it may be
        # cleared is not this function's business.
        if attr is None or not hasattr(attr, "columns") or not attr.columns:
            continue
        column = attr.columns[0]
        # Primary keys are non-nullable but are never in an update body; excluding them keeps the error
        # message about fields the caller actually chose.
        if not column.nullable and not column.primary_key:
            unclearable.append(wire_name)

    if unclearable:
        raise ValueError(
            f"These fields cannot be cleared because they must always hold a value: "
            f"{sorted(unclearable)}"
        )
