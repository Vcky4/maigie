"""Finance domain — the ``LedgerLine`` table.

Already exists in the database (Prisma-era, currently empty), so **no migration** — this model mirrors
the live schema (naive timestamps, ``updatedAt`` set on write, mirrored indexes), like the other
pre-existing tables the dashboard adopts.

This is manual operator bookkeeping, and it is the one place a GBP base and FX conversion are correct:
unlike commercial revenue (`PlusPurchase`), whose per-currency amounts must never be blended, a books
ledger exists precisely to express every line in one reporting currency. ``amountGbp`` is stored per
row so totals are a plain ``SUM`` and never a live conversion, and ``fxSource``/``gbpPerUnit``/
``fxAsOfDate`` record how each conversion was arrived at so a total can always explain itself.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database.base import Base


class LedgerLine(Base):
    """One income or expense line in the internal books."""

    __tablename__ = "LedgerLine"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    #: INCOME | EXPENSE.
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: As entered, in ``currency``.
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    #: The line in the reporting base (GBP). Stored, not computed on read, so a report is a SUM.
    amount_gbp: Mapped[Decimal] = mapped_column("amountGbp", Numeric, nullable=False)
    #: GBP per one unit of ``currency`` for this line. Null when entered directly in GBP terms.
    gbp_per_unit: Mapped[Decimal | None] = mapped_column("gbpPerUnit", Numeric, nullable=True)
    #: The date the rate is as of (string, e.g. "2026-09-10"), when a dated rate was used.
    fx_as_of_date: Mapped[str | None] = mapped_column("fxAsOfDate", String, nullable=True)
    #: How the GBP figure was arrived at: "same" (already GBP) | "manual" (operator-entered GBP).
    fx_source: Mapped[str] = mapped_column("fxSource", String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column("occurredAt", DateTime, nullable=False)
    #: The admin who recorded the line.
    created_by_id: Mapped[str | None] = mapped_column("createdById", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime, nullable=False)

    __table_args__ = (
        Index("LedgerLine_occurredAt_idx", "occurredAt"),
        Index("LedgerLine_kind_occurredAt_idx", "kind", "occurredAt"),
    )

    def __repr__(self) -> str:
        return f"<LedgerLine id={self.id} kind={self.kind} {self.amount} {self.currency}>"
