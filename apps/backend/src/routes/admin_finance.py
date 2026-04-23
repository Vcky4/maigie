"""
Super-admin finance ledger: income and expenses with GBP equivalents.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.dependencies import DBDep, SuperAdminUser
from src.services.audit_service import log_admin_action
from src.services.fx_service import convert_amount_to_gbp, list_fx_currencies, normalize_currency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/finance", tags=["admin-finance"])

LedgerKind = Literal["INCOME", "EXPENSE"]


class LedgerLineResponse(BaseModel):
    id: str
    kind: LedgerKind
    title: str
    description: str | None
    amount: str
    currency: str
    amountGbp: str
    gbpPerUnit: str | None
    fxAsOfDate: str | None
    fxSource: str
    occurredAt: datetime
    createdById: str | None
    createdAt: datetime
    updatedAt: datetime


class LedgerLineCreate(BaseModel):
    kind: LedgerKind
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(None, max_length=4000)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    occurredAt: datetime
    """If set, skips FX lookup and stores this GBP total (manual override)."""
    amountGbp: Decimal | None = Field(None, gt=0)


class LedgerLineUpdate(BaseModel):
    kind: LedgerKind | None = None
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, max_length=4000)
    amount: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    occurredAt: datetime | None = None
    amountGbp: Decimal | None = Field(None, gt=0)


class LedgerListResponse(BaseModel):
    items: list[LedgerLineResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int
    sumIncomeGbp: str
    sumExpenseGbp: str
    netGbp: str
    avgMonthlyExpenseGbp: str = Field(
        description="Total expenses divided by inclusive calendar months from first to last expense (minimum one month).",
    )


class FxPreviewResponse(BaseModel):
    amount: str
    currency: str
    amountGbp: str
    gbpPerUnit: str | None
    fxAsOfDate: str | None
    fxSource: str


def _to_response(row) -> LedgerLineResponse:
    return LedgerLineResponse(
        id=row.id,
        kind=str(row.kind),
        title=row.title,
        description=row.description,
        amount=str(row.amount),
        currency=row.currency,
        amountGbp=str(row.amountGbp),
        gbpPerUnit=str(row.gbpPerUnit) if row.gbpPerUnit is not None else None,
        fxAsOfDate=row.fxAsOfDate,
        fxSource=row.fxSource,
        occurredAt=row.occurredAt,
        createdById=row.createdById,
        createdAt=row.createdAt,
        updatedAt=row.updatedAt,
    )


@router.get("/currencies", response_model=dict[str, str])
async def get_fx_currencies(_super: SuperAdminUser):
    try:
        return await list_fx_currencies()
    except Exception as e:
        logger.exception("Failed to load FX currencies")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load currency list: {e}",
        ) from e


@router.get("/fx-preview", response_model=FxPreviewResponse)
async def preview_fx(
    _super: SuperAdminUser,
    amount: Decimal = Query(..., gt=0),
    currency: str = Query(..., min_length=3, max_length=3),
):
    try:
        cur = normalize_currency(currency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        amount_gbp, gbp_per, fx_date, fx_src = await convert_amount_to_gbp(amount, cur)
    except Exception as e:
        logger.warning("FX preview failed: %s", e)
        raise HTTPException(
            status_code=400,
            detail=str(e),
        ) from e

    return FxPreviewResponse(
        amount=str(amount),
        currency=cur,
        amountGbp=str(amount_gbp),
        gbpPerUnit=str(gbp_per) if cur != "GBP" else "1",
        fxAsOfDate=fx_date or None,
        fxSource=fx_src,
    )


@router.get("/ledger", response_model=LedgerListResponse)
async def list_ledger(
    _super: SuperAdminUser,
    db: DBDep,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    kind: LedgerKind | None = None,
    fromDate: datetime | None = None,
    toDate: datetime | None = None,
):
    where: dict = {}
    if kind:
        where["kind"] = kind
    if fromDate or toDate:
        where["occurredAt"] = {}
        if fromDate:
            where["occurredAt"]["gte"] = fromDate
        if toDate:
            where["occurredAt"]["lte"] = toDate

    total = await db.ledgerline.count(where=where)
    skip = (page - 1) * pageSize
    rows = await db.ledgerline.find_many(
        where=where,
        order={"occurredAt": "desc"},
        skip=skip,
        take=pageSize,
    )

    # prisma-client-py find_many does not support `select=`; fetch rows and sum in process.
    income_only = await db.ledgerline.find_many(where={**where, "kind": "INCOME"})
    expense_only = await db.ledgerline.find_many(where={**where, "kind": "EXPENSE"})
    inc_d = sum((Decimal(str(r.amountGbp)) for r in income_only), Decimal("0"))
    exp_d = sum((Decimal(str(r.amountGbp)) for r in expense_only), Decimal("0"))

    if expense_only:
        dates = [r.occurredAt for r in expense_only]
        mn, mx = min(dates), max(dates)
        month_span = (mx.year - mn.year) * 12 + (mx.month - mn.month) + 1
        month_span = max(1, month_span)
        avg_monthly_exp = (exp_d / Decimal(month_span)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        avg_monthly_exp = Decimal("0")

    total_pages = (total + pageSize - 1) // pageSize if total else 0

    return LedgerListResponse(
        items=[_to_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=total_pages,
        sumIncomeGbp=str(inc_d),
        sumExpenseGbp=str(exp_d),
        netGbp=str(inc_d - exp_d),
        avgMonthlyExpenseGbp=str(avg_monthly_exp),
    )


@router.post("/ledger", response_model=LedgerLineResponse)
async def create_ledger_line(
    body: LedgerLineCreate,
    admin_user: SuperAdminUser,
    db: DBDep,
):
    try:
        cur = normalize_currency(body.currency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if body.amountGbp is not None:
        amount_gbp = body.amountGbp
        gbp_per = (amount_gbp / body.amount) if body.amount != 0 else None
        fx_date = None
        fx_src = "manual"
    else:
        try:
            amount_gbp, gbp_per, fx_date, fx_src = await convert_amount_to_gbp(body.amount, cur)
        except Exception as e:
            logger.warning("FX conversion failed on create: %s", e)
            raise HTTPException(status_code=400, detail=str(e)) from e

    row = await db.ledgerline.create(
        data={
            "kind": body.kind,
            "title": body.title.strip(),
            "description": body.description.strip() if body.description else None,
            "amount": body.amount,
            "currency": cur,
            "amountGbp": amount_gbp,
            "gbpPerUnit": gbp_per,
            "fxAsOfDate": fx_date or None,
            "fxSource": fx_src,
            "occurredAt": body.occurredAt,
            "createdById": admin_user.id,
        }
    )

    await log_admin_action(
        admin_user.id,
        "create_ledger_line",
        "ledger_line",
        resource_id=row.id,
        details={"kind": body.kind, "currency": cur, "fxSource": fx_src},
        db_client=db,
    )
    return _to_response(row)


@router.patch("/ledger/{line_id}", response_model=LedgerLineResponse)
async def update_ledger_line(
    line_id: str,
    body: LedgerLineUpdate,
    admin_user: SuperAdminUser,
    db: DBDep,
):
    existing = await db.ledgerline.find_unique(where={"id": line_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Ledger line not found")

    data: dict = {}
    if body.kind is not None:
        data["kind"] = body.kind
    if body.title is not None:
        data["title"] = body.title.strip()
    if body.description is not None:
        data["description"] = body.description.strip() if body.description else None
    if body.occurredAt is not None:
        data["occurredAt"] = body.occurredAt

    amount = Decimal(str(existing.amount)) if body.amount is None else body.amount
    currency = existing.currency
    if body.currency is not None:
        try:
            currency = normalize_currency(body.currency)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        data["currency"] = currency
    if body.amount is not None:
        data["amount"] = body.amount

    needs_fx = body.amountGbp is None and (body.amount is not None or body.currency is not None)
    if body.amountGbp is not None:
        data["amountGbp"] = body.amountGbp
        data["gbpPerUnit"] = (body.amountGbp / amount) if amount != 0 else None
        data["fxAsOfDate"] = None
        data["fxSource"] = "manual"
    elif needs_fx:
        try:
            amount_gbp, gbp_per, fx_date, fx_src = await convert_amount_to_gbp(amount, currency)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        data["amountGbp"] = amount_gbp
        data["gbpPerUnit"] = gbp_per
        data["fxAsOfDate"] = fx_date or None
        data["fxSource"] = fx_src

    if not data:
        return _to_response(existing)

    row = await db.ledgerline.update(where={"id": line_id}, data=data)

    await log_admin_action(
        admin_user.id,
        "update_ledger_line",
        "ledger_line",
        resource_id=line_id,
        details={"fields": list(data.keys())},
        db_client=db,
    )
    return _to_response(row)


@router.delete("/ledger/{line_id}")
async def delete_ledger_line(
    line_id: str,
    admin_user: SuperAdminUser,
    db: DBDep,
):
    existing = await db.ledgerline.find_unique(where={"id": line_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Ledger line not found")

    await db.ledgerline.delete(where={"id": line_id})
    await log_admin_action(
        admin_user.id,
        "delete_ledger_line",
        "ledger_line",
        resource_id=line_id,
        db_client=db,
    )
    return {"ok": True}
