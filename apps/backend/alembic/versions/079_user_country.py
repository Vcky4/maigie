"""Add User.country — the learner's market, and the source of truth for pricing currency.

Per-market pricing (§6.8) needs to know a learner's country: `NG` prices the catalogue in NGN and
routes payment through Paystack, everything else is the USD list on Stripe. Until now the client
guessed this from device locale/timezone, which is wrong for a travelling learner or an en-US phone
and silently mispriced them. This stores the fact instead, set at signup/onboarding and editable in
profile.

Nullable with no default and no backfill: `None` means "not asked yet", which the resolver reads as
the USD default and the clients turn into a one-time "confirm your country" prompt. A backfilled
default would be indistinguishable from a deliberate choice — the same reasoning `timezoneSource`
uses on preferences. The down-revision drops it (safe — currency falls back to USD without it).
"""

import sqlalchemy as sa

from alembic import op

revision = "079_user_country"
down_revision = "078_drop_legacy_pref_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("User", sa.Column("country", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("User", "country")
