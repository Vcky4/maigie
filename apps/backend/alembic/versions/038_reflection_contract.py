"""Replace the reflection's three untyped JSON layers with one typed `metrics` object.

`Reflection` stored `activitiesLayer`, `progressLayer` and `achievementsLayer` as untyped
JSON, and `reflection_service` filled them from a language model that had been asked for
`topics_studied`, `sessions_completed`, `notes_created`, `total_minutes`,
`concepts_mastered`, `retention_score`, `streak_days` and `milestones` — while the only
context it was given was the behaviour profile. No session, note, topic, flashcard, quiz or
achievement row was read. The model produced counts for data it had never seen, and the row
recorded them beside a real `periodStart` as though they had been measured. On failure the
service wrote hardcoded zeros, so a broken generation and a genuinely inactive week left
identical rows.

The missing type is what let that persist, and it caused a second defect on the way out:
the keys were snake_case inside a camelCase payload, so the one client reading them got
`undefined` for every field, and four of the fields it wanted were never written under any
spelling.

`metrics` replaces all three. It is NOT NULL, defaults to `{}`, and is filled by SQL rather
than by generation. Absence is expressed by a null field *inside* the object, which keeps
"not measured" distinct from "measured as zero" — a distinction the zero-filled failure path
destroyed.

The three columns are dropped rather than kept for a later migration to replace, because
their contents are model-invented counts and nothing downstream can use them.

**The shared database does hold 13 of these rows, across 6 users.** An earlier draft of this
file said otherwise. Inspecting them changed two things here: the `recommendations` reset
below had to be widened (see its comment), and `type` normalisation had to cope with a third
value, `daily`, which no code path was supposed to produce.

Ten of those thirteen rows also carry invented figures *inside the summary prose* — "averaging
4.6 minutes", "completed 4 study sessions" — which dropping a JSON column cannot reach. Whether
those rows survive is a product decision recorded in the plan, not something this migration
takes on itself: it leaves every row in place and only repairs shapes.

`type` gets a CHECK and is normalised to lowercase. It was an unconstrained String, and the
task wrote `"WEEKLY"` past a service branching on `"weekly"` — so the row took the fallback
period and then failed the equality filter on the list endpoint. The UPDATE runs before the
constraint so an existing row cannot fail it.

The unique index on `(userId, type, periodStart)` is what makes generation idempotent.
Nothing stopped two rows existing for the same week, and the library page counts rows, so
the count was of generation attempts rather than of periods reflected on. Duplicates are
collapsed before the index is built, keeping the most recently created row of each group,
because that is the one written by the most recent code.

`title`, `depth` and `openedAt` are new: a heading the page can show, which depth the
learner actually received (the tier already decided this and already charged for it, but
nothing published it), and a first-opened marker set by an explicit read route rather than
as a side effect of the GET.

Revision ID: 038_reflection_contract
Revises: 037_add_deck_origin
Create Date: 2026-08-19
"""

import sqlalchemy as sa

from alembic import op

revision = "038_reflection_contract"
down_revision = "037_add_deck_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- New columns -------------------------------------------------------
    op.add_column("Reflection", sa.Column("title", sa.String(), nullable=True))
    op.add_column(
        "Reflection",
        sa.Column("depth", sa.String(), nullable=False, server_default="standard"),
    )
    op.add_column(
        "Reflection",
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("Reflection", sa.Column("openedAt", sa.DateTime(timezone=True), nullable=True))

    # --- `recommendations` becomes NOT NULL, holding a list of objects ------
    # It was nullable JSON annotated as `dict` while the writer stored a list of **strings**.
    # The new response type is `list[ReflectionAction]`, so a surviving array of strings is
    # not merely untidy: `ReflectionResponse.model_validate` raises on it, and any
    # `GET /reflections` page containing such a row would return 500.
    #
    # An earlier draft reset only non-arrays, which let exactly that through — an array of
    # strings is a perfectly good array. The check below resets anything that is not an array
    # of objects.
    #
    # The prose is deliberately not converted into actions. A legacy recommendation reads
    # "extend your average session length from 4.6 minutes", where 4.6 was invented by a model
    # that had never seen a session, so preserving the text would republish a fabricated
    # measurement inside a newly typed field.
    op.execute(
        sa.text("""UPDATE "Reflection" SET recommendations = '[]' WHERE recommendations IS NULL""")
    )
    op.execute(
        sa.text(
            """
            UPDATE "Reflection"
               SET recommendations = '[]'
             WHERE json_typeof(recommendations::json) <> 'array'
                OR EXISTS (
                    SELECT 1
                      FROM json_array_elements(recommendations::json) AS element
                     WHERE json_typeof(element) <> 'object'
                )
            """
        )
    )
    op.alter_column(
        "Reflection",
        "recommendations",
        nullable=False,
        server_default=sa.text("'[]'"),
    )

    # --- Drop the fabricated layers ---------------------------------------
    op.drop_column("Reflection", "activitiesLayer")
    op.drop_column("Reflection", "progressLayer")
    op.drop_column("Reflection", "achievementsLayer")

    # --- Normalise `type`, then constrain it ------------------------------
    op.execute(sa.text("""UPDATE "Reflection" SET type = lower(type)"""))
    # Anything that is neither weekly nor monthly is relabelled `weekly`, and in this database
    # that is not a guess. One row carries `type = 'daily'`, which no branch of the old service
    # could produce a period for — it fell through to the weekly default — and its stored span
    # is indeed 7 days. Every one of the 13 existing rows spans 7 days. So relabelling makes
    # the row agree with the period it already has, rather than overwriting a real cadence.
    #
    # A future value that did carry its own period would need handling here rather than being
    # swept into `weekly`, which is why this comment names the evidence it relied on.
    op.execute(
        sa.text(
            """UPDATE "Reflection" SET type = 'weekly' WHERE type NOT IN ('weekly', 'monthly')"""
        )
    )
    op.create_check_constraint(
        "Reflection_type_check", "Reflection", "type IN ('weekly', 'monthly')"
    )
    op.create_check_constraint(
        "Reflection_depth_check", "Reflection", "depth IN ('standard', 'deep')"
    )

    # --- Collapse duplicate periods, then enforce one per period ----------
    # Built after the delete so a bad delete fails here, loudly, rather than leaving
    # duplicates behind a constraint that was created before the data was ready.
    op.execute(
        sa.text(
            """
            DELETE FROM "Reflection"
             WHERE id IN (
                SELECT id FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY "userId", type, "periodStart"
                            ORDER BY "createdAt" DESC, id DESC
                        ) AS rn
                    FROM "Reflection"
                ) ranked
                WHERE rn > 1
             )
            """
        )
    )
    op.create_unique_constraint(
        "Reflection_userId_type_period_key",
        "Reflection",
        ["userId", "type", "periodStart"],
    )


def downgrade() -> None:
    op.drop_constraint("Reflection_userId_type_period_key", "Reflection", type_="unique")
    op.drop_constraint("Reflection_depth_check", "Reflection", type_="check")
    op.drop_constraint("Reflection_type_check", "Reflection", type_="check")

    # The layers come back empty. Their previous contents were model-invented counts, so
    # restoring the columns restores the shape and deliberately not the values — there is
    # nothing here worth putting back.
    op.add_column("Reflection", sa.Column("achievementsLayer", sa.JSON(), nullable=True))
    op.add_column("Reflection", sa.Column("progressLayer", sa.JSON(), nullable=True))
    op.add_column("Reflection", sa.Column("activitiesLayer", sa.JSON(), nullable=True))

    op.alter_column("Reflection", "recommendations", nullable=True, server_default=None)

    op.drop_column("Reflection", "openedAt")
    op.drop_column("Reflection", "metrics")
    op.drop_column("Reflection", "depth")
    op.drop_column("Reflection", "title")
