"""Finance domain — Pydantic schemas.

Monetary values are strings on the wire (matching the client contract): a decimal amount rendered
through a float loses precision, and money should not.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

LEDGER_KINDS = frozenset({"INCOME", "EXPENSE"})


class LedgerLineResponse(BaseModel):
    id: str
    kind: str
    title: str
    description: str | None = None
    amount: str
    currency: str
    amountGbp: str
    gbpPerUnit: str | None = None
    fxAsOfDate: str | None = None
    fxSource: str
    occurredAt: datetime
    createdById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class LedgerLineListResponse(BaseModel):
    items: list[LedgerLineResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int
    sumIncomeGbp: str
    sumExpenseGbp: str
    netGbp: str
    avgMonthlyExpenseGbp: str


class LedgerLineCreateRequest(BaseModel):
    kind: str
    title: str
    description: str | None = None
    amount: Decimal
    currency: str
    occurredAt: datetime
    #: Optional operator-supplied GBP figure. Required when ``currency`` is not GBP (no FX source is
    #: configured), and ignored/derived when it is GBP.
    amountGbp: Decimal | None = None


class LedgerLineUpdateRequest(BaseModel):
    kind: str | None = None
    title: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    occurredAt: datetime | None = None
    amountGbp: Decimal | None = None


class FxPreviewResponse(BaseModel):
    amount: str
    currency: str
    amountGbp: str
    gbpPerUnit: str | None = None
    fxAsOfDate: str | None = None
    fxSource: str
