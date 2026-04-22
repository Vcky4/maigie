"""
FX helpers for admin ledger (GBP reporting).

Uses Frankfurter (ECB-based, no API key). Unsupported ISO codes fall back to manual GBP entry.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

# Canonical host (api.frankfurter.app 301s to api.frankfurter.dev/v1 — see frankfurter docs).
FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
_Q4 = Decimal("0.0001")
_Q10 = Decimal("0.0000000001")


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_Q4, rounding=ROUND_HALF_UP)


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(_Q10, rounding=ROUND_HALF_UP)


def normalize_currency(currency: str) -> str:
    cur = currency.strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code")
    return cur


async def convert_amount_to_gbp(
    amount: Decimal, currency: str
) -> tuple[Decimal, Decimal, str, str]:
    """
    Convert `amount` from `currency` to GBP.

    Returns:
        amount_gbp, gbp_per_unit, fx_as_of_date (YYYY-MM-DD or empty), fx_source
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    cur = normalize_currency(currency)
    if cur == "GBP":
        gbp = _quantize_money(amount)
        return gbp, Decimal("1"), "", "identity"

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(
            f"{FRANKFURTER_BASE}/latest",
            params={"from": cur, "to": "GBP"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"FX provider returned HTTP {resp.status_code}; "
                "try another currency or set amountGbp manually."
            )
        payload: dict[str, Any] = resp.json()

    rates = payload.get("rates") or {}
    rate_raw = rates.get("GBP")
    if rate_raw is None:
        raise RuntimeError(
            f"No GBP rate returned for {cur}. "
            "Frankfurter may not list this currency; set amountGbp manually."
        )

    gbp_per_unit = _quantize_rate(Decimal(str(rate_raw)))
    amount_gbp = _quantize_money(amount * gbp_per_unit)
    fx_date = str(payload.get("date") or "")
    return amount_gbp, gbp_per_unit, fx_date, "frankfurter"


async def list_fx_currencies() -> dict[str, str]:
    """ISO code -> currency label (for admin UI)."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(f"{FRANKFURTER_BASE}/currencies")
        resp.raise_for_status()
        data: dict[str, str] = resp.json()
    return dict(sorted(data.items(), key=lambda kv: kv[0]))
