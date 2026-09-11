"""Finance domain — API routes (super admin only).

The internal income/expense ledger. Mounted under ``/api/v1/admin/finance``.

FX is deliberately **manual**: there is no exchange-rate provider wired into the backend, so rather
than fabricate a rate, a non-GBP line requires the operator to supply the GBP figure, and every line
records how its GBP value was arrived at (``fxSource``). A live rate source can be layered in later
without changing the stored shape — that is what ``fxAsOfDate``/``gbpPerUnit`` are already for.
"""

import logging
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.shared.auth import SuperAdminUser
from src.shared.database import get_session_factory

from . import models
from .db_models import LedgerLine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["finance"])

_GBP = "GBP"

#: Currencies the ledger offers. Labels only — an honest, curated list, not an FX table.
CURRENCIES: dict[str, str] = {
    "GBP": "Pound sterling",
    "USD": "US dollar",
    "EUR": "Euro",
    "NGN": "Nigerian naira",
    "CAD": "Canadian dollar",
    "AUD": "Australian dollar",
    "INR": "Indian rupee",
    "ZAR": "South African rand",
    "KES": "Kenyan shilling",
    "GHS": "Ghanaian cedi",
}


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _money(value: Decimal | None) -> str | None:
    """A 2dp string, or None passed through. Money is rendered as a string to keep its precision."""
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def _rate(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.000001")))


def _resolve_gbp(
    amount: Decimal, currency: str, amount_gbp: Decimal | None
) -> tuple[Decimal, Decimal | None, str]:
    """Return (amountGbp, gbpPerUnit, fxSource). Raises 400 for a non-GBP line with no GBP supplied.

    Never invents a rate: GBP lines are 1:1 (``same``); everything else must carry an operator-entered
    GBP figure (``manual``), from which the per-unit rate is derived rather than fetched.
    """
    currency = currency.upper()
    if currency == _GBP:
        return amount, Decimal(1), "same"
    if amount_gbp is None:
        raise HTTPException(
            status_code=400,
            detail="amountGbp is required for a non-GBP amount (no FX rate source is configured)",
        )
    per_unit = (amount_gbp / amount) if amount != 0 else None
    return amount_gbp, per_unit, "manual"


def _response(row: LedgerLine) -> models.LedgerLineResponse:
    return models.LedgerLineResponse(
        id=row.id,
        kind=row.kind,
        title=row.title,
        description=row.description,
        amount=str(row.amount),
        currency=row.currency,
        amountGbp=_money(row.amount_gbp) or "0.00",
        gbpPerUnit=_rate(row.gbp_per_unit),
        fxAsOfDate=row.fx_as_of_date,
        fxSource=row.fx_source,
        occurredAt=row.occurred_at,
        createdById=row.created_by_id,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


@router.get("/finance/currencies")
async def list_currencies(admin_user: SuperAdminUser) -> dict[str, str]:
    """The currencies the ledger accepts (code → name)."""
    return CURRENCIES


@router.get("/finance/fx-preview", response_model=models.FxPreviewResponse)
async def fx_preview(
    admin_user: SuperAdminUser,
    amount: Decimal = Query(...),
    currency: str = Query(...),
):
    """Preview the GBP conversion for an amount.

    GBP is 1:1. For any other currency there is no configured rate source, so the preview reports
    ``manual`` with an empty GBP figure — the operator supplies it on save rather than the server
    guessing one.
    """
    currency = currency.upper()
    if currency == _GBP:
        return models.FxPreviewResponse(
            amount=str(amount),
            currency=currency,
            amountGbp=_money(amount) or "0.00",
            gbpPerUnit="1.000000",
            fxAsOfDate=None,
            fxSource="same",
        )
    return models.FxPreviewResponse(
        amount=str(amount),
        currency=currency,
        amountGbp="",
        gbpPerUnit=None,
        fxAsOfDate=None,
        fxSource="manual",
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _naive(datetime.fromisoformat(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}")


@router.get("/finance/ledger", response_model=models.LedgerLineListResponse)
async def list_ledger(
    admin_user: SuperAdminUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    kind: str | None = Query(None),
    fromDate: str | None = Query(None),
    toDate: str | None = Query(None),
):
    """List ledger lines newest first, with GBP totals over the same filters (super admin only)."""
    conditions = []
    if kind:
        conditions.append(LedgerLine.kind == kind)
    from_dt = _parse_date(fromDate)
    to_dt = _parse_date(toDate)
    if from_dt:
        conditions.append(LedgerLine.occurred_at >= from_dt)
    if to_dt:
        conditions.append(LedgerLine.occurred_at <= to_dt)

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(LedgerLine).where(*conditions))
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(LedgerLine)
                    .where(*conditions)
                    .order_by(LedgerLine.occurred_at.desc())
                    .offset((page - 1) * pageSize)
                    .limit(pageSize)
                )
            )
            .scalars()
            .all()
        )

        income = (
            await session.execute(
                select(func.coalesce(func.sum(LedgerLine.amount_gbp), 0)).where(
                    *conditions, LedgerLine.kind == "INCOME"
                )
            )
        ).scalar() or Decimal(0)
        expense = (
            await session.execute(
                select(func.coalesce(func.sum(LedgerLine.amount_gbp), 0)).where(
                    *conditions, LedgerLine.kind == "EXPENSE"
                )
            )
        ).scalar() or Decimal(0)
        exp_bounds = (
            await session.execute(
                select(func.min(LedgerLine.occurred_at), func.max(LedgerLine.occurred_at)).where(
                    *conditions, LedgerLine.kind == "EXPENSE"
                )
            )
        ).one()

    income = Decimal(income)
    expense = Decimal(expense)
    avg_monthly_expense = Decimal(0)
    first_exp, last_exp = exp_bounds
    if first_exp and last_exp and expense != 0:
        months = (last_exp.year - first_exp.year) * 12 + (last_exp.month - first_exp.month) + 1
        months = max(1, months)
        avg_monthly_expense = expense / months

    return models.LedgerLineListResponse(
        items=[_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total else 0,
        sumIncomeGbp=_money(income) or "0.00",
        sumExpenseGbp=_money(expense) or "0.00",
        netGbp=_money(income - expense) or "0.00",
        avgMonthlyExpenseGbp=_money(avg_monthly_expense) or "0.00",
    )


@router.post("/finance/ledger", response_model=models.LedgerLineResponse, status_code=201)
async def create_ledger_line(body: models.LedgerLineCreateRequest, admin_user: SuperAdminUser):
    """Record an income or expense line (super admin only), audited."""
    from src.domains.admin.services.audit_service import log_admin_action

    if body.kind not in models.LEDGER_KINDS:
        raise HTTPException(status_code=400, detail="kind must be INCOME or EXPENSE")
    try:
        amount_gbp, per_unit, fx_source = _resolve_gbp(body.amount, body.currency, body.amountGbp)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="Invalid monetary amount")

    now = _naive_utc_now()
    row = LedgerLine(
        kind=body.kind,
        title=body.title,
        description=body.description,
        amount=body.amount,
        currency=body.currency.upper(),
        amount_gbp=amount_gbp,
        gbp_per_unit=per_unit,
        fx_as_of_date=None,
        fx_source=fx_source,
        occurred_at=_naive(body.occurredAt),
        created_by_id=admin_user.id,
        created_at=now,
        updated_at=now,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="create_ledger_line",
        resource_type="ledger_line",
        resource_id=row.id,
        details={"kind": row.kind, "amountGbp": _money(row.amount_gbp), "currency": row.currency},
    )
    return _response(row)


@router.patch("/finance/ledger/{line_id}", response_model=models.LedgerLineResponse)
async def update_ledger_line(
    line_id: str, body: models.LedgerLineUpdateRequest, admin_user: SuperAdminUser
):
    """Update a ledger line (super admin only), audited. Recomputes GBP when money fields change."""
    from src.domains.admin.services.audit_service import log_admin_action

    changes = body.model_dump(exclude_unset=True)
    if "kind" in changes and changes["kind"] not in models.LEDGER_KINDS:
        raise HTTPException(status_code=400, detail="kind must be INCOME or EXPENSE")

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(LedgerLine).where(LedgerLine.id == line_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger line not found")

        if "kind" in changes:
            row.kind = changes["kind"]
        if "title" in changes:
            row.title = changes["title"]
        if "description" in changes:
            row.description = changes["description"]
        if "occurredAt" in changes and changes["occurredAt"] is not None:
            row.occurred_at = _naive(body.occurredAt)

        # If any of amount/currency/amountGbp changed, re-resolve the GBP figure together.
        if {"amount", "currency", "amountGbp"} & set(changes):
            new_amount = body.amount if body.amount is not None else row.amount
            new_currency = (body.currency or row.currency).upper()
            # amountGbp: use the supplied value if present in this request, else keep existing only
            # when currency/amount did not change; otherwise force a re-supply for non-GBP.
            supplied_gbp = body.amountGbp if "amountGbp" in changes else None
            amount_gbp, per_unit, fx_source = _resolve_gbp(new_amount, new_currency, supplied_gbp)
            row.amount = new_amount
            row.currency = new_currency
            row.amount_gbp = amount_gbp
            row.gbp_per_unit = per_unit
            row.fx_source = fx_source

        row.updated_at = _naive_utc_now()
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_ledger_line",
        resource_type="ledger_line",
        resource_id=line_id,
        details={"fields": sorted(changes.keys())},
    )
    return _response(row)


@router.delete("/finance/ledger/{line_id}")
async def delete_ledger_line(line_id: str, admin_user: SuperAdminUser):
    """Delete a ledger line (super admin only), audited."""
    from src.domains.admin.services.audit_service import log_admin_action

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(LedgerLine).where(LedgerLine.id == line_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger line not found")
        await session.delete(row)
        await session.commit()

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_ledger_line",
        resource_type="ledger_line",
        resource_id=line_id,
        details={"kind": row.kind},
    )
    return {"ok": True}
