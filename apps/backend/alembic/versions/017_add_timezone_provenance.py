"""Record where a learner's timezone came from, so "UTC" stops being ambiguous.

``UserPreferences.timezone`` already exists, but it is ``NOT NULL`` with a server
default of ``"UTC"`` and nothing has ever prompted for it. So a row reading
``"UTC"`` means either *this learner is in UTC* or *we have never asked*, and
those are different facts. Converting a stored instant to local time on that
basis is guessing for the majority of rows, which is why nothing in the learning,
behaviour or study-plan path reads the column at all today.

Rather than reinterpret the existing column destructively — rewriting every
``"UTC"`` to ``NULL`` would assume no learner has ever legitimately been in UTC,
and the ``PUT /users/preferences`` route has always accepted the field — this adds
provenance alongside it:

``timezoneSource``    ``DEVICE`` | ``MANUAL``, or ``NULL`` for never captured.
``timezoneCapturedAt`` when that happened.

``NULL`` source is the honest reading of every existing row: the value is a
default, not an observation. Code that needs a *trustworthy* local time checks the
source; code that only needs a display fallback keeps reading ``timezone`` and
behaves exactly as before.

The distinction between ``DEVICE`` and ``MANUAL`` matters for precedence: a
learner who states their timezone outranks a device that reports one, and without
the source there is no way to stop a device report from silently overwriting a
deliberate choice on the learner's next visit.

No backfill. Nothing is inferred about existing rows, because nothing can be.

Revision ID: 017_add_timezone_provenance
Revises: 016_align_prepare_ui_fields
Create Date: 2026-08-11
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "017_add_timezone_provenance"
down_revision = "016_align_prepare_ui_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no server default. NULL is the point: it is the only way to
    # say "the timezone column holds a default, not a fact".
    op.add_column("UserPreferences", sa.Column("timezoneSource", sa.String(), nullable=True))
    op.add_column(
        "UserPreferences",
        sa.Column("timezoneCapturedAt", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("UserPreferences", "timezoneCapturedAt")
    op.drop_column("UserPreferences", "timezoneSource")
