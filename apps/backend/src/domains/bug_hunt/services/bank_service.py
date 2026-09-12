"""The Nigerian bank list, for the payout-account picker.

**Fetched from Paystack rather than hardcoded.** Nigeria has a hundred-odd banks and the list moves: banks
merge, microfinance banks appear, and a stale client-side array means a tester whose bank is missing simply
cannot be paid. Paystack is already the payment provider in this backend, so its list is the one that will
agree with any future transfer API — and it carries the bank *codes* a transfer needs, which a hand-written
list of names would not.

**Cached for a day.** The list changes on the order of months and a season lasts two weeks, so a daily fetch
is generous. The cache also means the picker does not put a third-party HTTP call on the critical path of a
form somebody is filling in.

**Degrades to empty rather than erroring.** If Paystack is unreachable, the endpoint returns an empty list and
the client falls back to free-text entry: a tester who knows their bank name should not be blocked from
getting paid because a provider API is down. The account number is what the transfer needs, and that is
validated locally.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from src.shared.infrastructure import cache

logger = logging.getLogger(__name__)

_CACHE_KEY = "bug_hunt:banks:ng"
_CACHE_SECONDS = 24 * 60 * 60
_TIMEOUT_SECONDS = 8.0


async def nigerian_banks() -> list[dict[str, str]]:
    """`[{"code": "058", "name": "Guaranty Trust Bank"}, …]`, sorted by name.

    Deduplicated on code, because Paystack lists some institutions more than once under different slugs and a
    picker with two "Access Bank" entries invites the tester to choose the wrong one.
    """
    cached = await cache.get(cache.make_key([_CACHE_KEY]))
    if isinstance(cached, list) and cached:
        return cached

    banks = await _fetch()
    if banks:
        await cache.set(cache.make_key([_CACHE_KEY]), banks, expire=_CACHE_SECONDS)
    return banks


async def _fetch() -> list[dict[str, str]]:
    import httpx

    from src.config import get_settings
    from src.domains.billing.services.paystack_service import PAYSTACK_BASE
    from src.shared.infrastructure import create_http_client

    settings = get_settings()
    if not settings.PAYSTACK_SECRET_KEY:
        # No key configured — in local development, for instance. An empty list is the honest answer and the
        # client falls back to typing a bank name.
        logger.info("bug_hunt: no Paystack key, so no bank list")
        return []

    try:
        async with create_http_client(timeout=httpx.Timeout(_TIMEOUT_SECONDS)) as client:
            response = await client.get(
                f"{PAYSTACK_BASE}/bank",
                params={"country": "nigeria", "perPage": 200},
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            )
        if response.status_code != 200:
            logger.warning("bug_hunt: bank list fetch returned %s", response.status_code)
            return []
        payload: Any = response.json()
    except Exception:
        # Deliberately swallowed. A tester who knows their bank name must not be blocked from being paid
        # because a provider API is having a minute.
        logger.warning("bug_hunt: bank list unavailable", exc_info=True)
        return []

    by_code: dict[str, dict[str, str]] = {}
    for entry in payload.get("data") or []:
        code = str(entry.get("code") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not code or not name or code in by_code:
            continue
        by_code[code] = {"code": code, "name": name}

    return sorted(by_code.values(), key=lambda bank: bank["name"])
