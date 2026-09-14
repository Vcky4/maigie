"""Comparing a column that is a Postgres enum here and a `varchar` there.

**The problem this solves is a production-only failure that no test can see.**

Several tables predate the SQLAlchemy migration and were created by Prisma, which renders a Prisma `enum`
as a real Postgres enum type. The SQLAlchemy models map those columns as `String`. For an `INSERT` that is
harmless: the parameter goes out untyped and Postgres infers the target column's type. For a comparison it
is fatal, because SQLAlchemy renders an explicit cast and Postgres has no operator for it::

    WHERE "Feedback".status = $1::VARCHAR
    -->  operator does not exist: "FeedbackStatus" = character varying

Observed in production on 2026-09-14, breaking `GET /admin/dashboard` and the `finance` income/expense
summary. Both had been shipped and reviewed, and both worked everywhere they were tried, because
**staging does not have these types at all**: `src/init_schema.py` builds tables with `create_all`, which
renders `Mapped[str]` as `varchar`. So writes succeed in production while filtered reads fail, and the
staging database cannot reproduce it. That asymmetry is the whole reason this helper exists rather than a
note in a review comment.

`cast(column, Text)` is the portable fix. `enum::text` is valid and indexable-by-expression in Postgres,
and `varchar::text` is a no-op, so one expression is correct against both schemas. That matters more than
elegance: the alternative — declaring the ORM column as a Postgres `ENUM` with `create_type=False` — would
be correct in production and **wrong on staging**, swapping a prod-only failure for a staging-only one.

The real fix is to stop the two schemas diverging. Until then, any comparison against one of these columns
goes through here.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, cast

#: The columns that are Postgres enums in production while the ORM maps them as `String`.
#:
#: Produced by `scripts/audit_enum_columns.py`, which reads `information_schema` and compares it against
#: the mapped models. Listed here so the set is reviewable and so the guard test can assert that the list
#: and the code agree; re-run the script after any Prisma-era table changes shape.
#:
#: `Feedback.type`, `Achievement.achievementType` and `ScheduleBehaviourLog.behaviourType` are included
#: even though nothing filters on them today. They are the same landmine and cost nothing to record.
ENUM_BACKED_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("Achievement", "achievementType"),
        ("CareerApplication", "status"),
        ("Feedback", "status"),
        ("Feedback", "type"),
        ("LedgerLine", "kind"),
        ("ScheduleBehaviourLog", "behaviourType"),
    }
)


def enum_text(column: Any) -> Any:
    """The column as `text`, for comparing against a Python string.

    Use for every filter on a column in :data:`ENUM_BACKED_COLUMNS`::

        conditions.append(enum_text(Feedback.status) == status)
        conditions.append(enum_text(LedgerLine.kind).in_(("INCOME", "EXPENSE")))

    Safe on a plain `varchar` column too, so applying it where it is not strictly needed costs nothing but
    a cast Postgres discards. Do **not** use it on an `INSERT` value: inserts already work, and casting
    there would break the type inference that makes them work.
    """
    return cast(column, Text)
