"""Landing drafts — SQLAlchemy models.

One table, `LandingDraft`, holding what an anonymous visitor built on the marketing site before
they had an account.

**Why this is a table of its own rather than an early `LearningProfile`.** The obvious shortcut is
to write the visitor's answers straight into the personal-learning tables and attach a user later.
That produces user-owned rows with no user: every query in `personal_learning` is scoped by
`userId`, so those rows are either orphans nothing can read or they need a nullable owner on tables
where ownership is the invariant. Worse, most drafts are never claimed — a landing page converts a
minority of visitors — so the majority of that content would be ghost data in the learner's own
tables, indistinguishable from real content that a learner abandoned.

Keeping it here means the draft is a *separate kind of thing* with its own lifecycle (open →
claimed, or expired), and nothing enters the learner's workspace until a real account claims it.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base, TimestampMixin


class LandingDraft(Base, TimestampMixin):
    """A starter setup built anonymously on maigie.com, awaiting a signup to claim it."""

    __tablename__ = "LandingDraft"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex[:25])

    # **The hash, never the token.** The raw token is returned once at creation and lives only in
    # the visitor's browser and the signup URL. Storing a hash means a database dump does not hand
    # over the ability to read or claim other people's drafts — the same reasoning as a password,
    # for the same reason: this string is the only thing standing between a stranger and the row.
    #
    # Unique, because the token is how a draft is looked up; a collision would be a cross-visitor
    # read.
    token_hash: Mapped[str] = mapped_column(
        "tokenHash", String, nullable=False, unique=True, index=True
    )

    # --- What the visitor told us ---
    #
    # All nullable. A draft is created by the first chip click, before there is anything else to
    # say, and a visitor who leaves after step 1 still leaves a usable row: the purpose alone is
    # enough to shape onboarding if they come back and sign up.
    # The visitor's address, asked for in the wizard before the preview.
    #
    # Nullable and never a credential: the token still authorises the row. This exists so an
    # abandoned draft is a person who can be followed up rather than an anonymous row, and so signup
    # can prefill the address they already typed.
    email: Mapped[str | None] = mapped_column("email", String, nullable=True, index=True)

    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    subjects: Mapped[list | None] = mapped_column("subjects", JSONB, nullable=True)
    goals_text: Mapped[str | None] = mapped_column("goalsText", Text, nullable=True)
    exam_name: Mapped[str | None] = mapped_column("examName", String, nullable=True)
    # `Date`, not `DateTime`, matching `LearningProfile.examDate` — an exam is on a day. Storing a
    # timestamp here would mean converting at claim time and inventing a time of day to do it.
    exam_date: Mapped[date | None] = mapped_column("examDate", Date, nullable=True)

    # --- The generated preview ---
    #
    # Stays on the draft rather than being materialised into prep/topics/flashcards, for the ghost
    # data reason in the module docstring. At claim time it is the learner's real auto-setup that
    # produces content; this is the sketch that persuaded them to sign up.
    preview: Mapped[list | None] = mapped_column("preview", JSONB, nullable=True)
    # Counted, not just flagged, because the cap is "one generate and one regenerate" rather than
    # "once". A visitor who changes their subject deserves a second look at it; a script does not
    # deserve a thousand.
    generate_count: Mapped[int] = mapped_column(
        "generateCount", Integer, nullable=False, default=0, server_default="0"
    )

    # --- Lifecycle ---
    #
    # `expired` is a status a sweep can write, but expiry is *true from `expiresAt`* whether or not
    # anything has run. Reads compare the timestamp rather than trusting the column, so an unswept
    # draft is already unusable — the same lazy-expiry shape `entitlement_service` uses for passes,
    # and for the same reason: a learner's access must not depend on a job having fired.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", server_default="open"
    )
    expires_at: Mapped[datetime] = mapped_column(
        "expiresAt", DateTime(timezone=True), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        "claimedAt", DateTime(timezone=True), nullable=True
    )
    # `SET NULL` rather than `CASCADE`: if the claiming user is later deleted, the draft row should
    # lose its link, not vanish. It is the record that a conversion happened, which is a fact about
    # the marketing site and outlives any one account.
    claimed_by: Mapped[str | None] = mapped_column(
        "claimedBy", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # The payload shape this row was written with. A draft can sit for a week while the wizard
    # ships a new question, so the claim path has to know which vocabulary it is reading rather
    # than assuming the current one.
    schema_version: Mapped[int] = mapped_column(
        "schemaVersion", Integer, nullable=False, default=1, server_default="1"
    )

    __table_args__ = (
        # The sweep's query: everything past its date that nobody has resolved yet.
        Index("LandingDraft_status_expiresAt_idx", "status", "expiresAt"),
        CheckConstraint(
            "status IN ('open', 'claimed', 'expired')",
            name="LandingDraft_status_check",
        ),
        # Claimed means claimed *by someone, at some point*. Without this, a bug that marked the
        # status without writing the link would produce a row that says a conversion happened and
        # cannot say whose — and the analytics built on it would be quietly wrong rather than
        # loudly broken.
        CheckConstraint(
            '(status <> \'claimed\') OR ("claimedBy" IS NOT NULL AND "claimedAt" IS NOT NULL)',
            name="LandingDraft_claimed_link_check",
        ),
    )

    def __repr__(self) -> str:
        return f"<LandingDraft id={self.id} status={self.status} purpose={self.purpose}>"
