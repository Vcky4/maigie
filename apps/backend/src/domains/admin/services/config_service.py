"""Admin system + LLM configuration, backed by the `SystemConfig` key/value table.

Only non-secret operational config lives here (maintenance mode, feature flags, LLM routing
preferences). It never reads or writes API keys or other secrets — those stay in the environment.

Two honesty notes:
- `creditLimits` is always empty: credit caps were retired (Decision 5), so there is nothing to show.
- LLM config is a stored key/value the app can read; wiring the LLM path to actually consume it is a
  separate follow-up. This surface persists preferences honestly rather than pretending to change a
  runtime the store is not yet wired into.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from src.shared.database import get_session_factory

from .. import models

_MAINTENANCE_KEY = "maintenanceMode"
_FEATURE_FLAG_CATEGORY = "featureFlag"
_LLM_CATEGORY = "llm"


async def _rows(session, *, category: str | None = None, key: str | None = None):
    if key is not None:
        return (
            await session.execute(
                text('SELECT key, value, category FROM "SystemConfig" WHERE key = :k'), {"k": key}
            )
        ).all()
    return (
        await session.execute(
            text('SELECT key, value, category FROM "SystemConfig" WHERE category = :c'),
            {"c": category},
        )
    ).all()


async def _upsert(
    session, *, key: str, value: str, category: str, label: str | None = None
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = (
        await session.execute(text('SELECT id FROM "SystemConfig" WHERE key = :k'), {"k": key})
    ).first()
    if existing:
        await session.execute(
            text(
                'UPDATE "SystemConfig" SET value = :v, category = :c, "updatedAt" = :t WHERE key = :k'
            ),
            {"v": value, "c": category, "t": now, "k": key},
        )
    else:
        await session.execute(
            text(
                'INSERT INTO "SystemConfig" (id, key, value, category, label, "createdAt", '
                '"updatedAt") VALUES (:id, :k, :v, :c, :label, :t, :t)'
            ),
            {
                "id": uuid.uuid4().hex[:25],
                "k": key,
                "v": value,
                "c": category,
                "label": label,
                "t": now,
            },
        )


async def get_system_config() -> models.SystemConfigResponse:
    factory = get_session_factory()
    async with factory() as session:
        maintenance = await _rows(session, key=_MAINTENANCE_KEY)
        flags = await _rows(session, category=_FEATURE_FLAG_CATEGORY)
    maintenance_mode = bool(maintenance and str(maintenance[0][1]).lower() == "true")
    feature_flags = {row[0]: str(row[1]).lower() == "true" for row in flags}
    return models.SystemConfigResponse(
        creditLimits={},  # retired model — nothing to show
        maintenanceMode=maintenance_mode,
        featureFlags=feature_flags,
    )


async def update_system_config(
    body: models.SystemConfigUpdateRequest,
) -> models.SystemConfigResponse:
    factory = get_session_factory()
    async with factory() as session:
        if body.maintenanceMode is not None:
            await _upsert(
                session,
                key=_MAINTENANCE_KEY,
                value="true" if body.maintenanceMode else "false",
                category="system",
                label="Maintenance mode",
            )
        if body.featureFlags is not None:
            for name, enabled in body.featureFlags.items():
                await _upsert(
                    session,
                    key=name,
                    value="true" if enabled else "false",
                    category=_FEATURE_FLAG_CATEGORY,
                )
        # creditLimits is intentionally ignored — the credit model is retired.
        await session.commit()
    return await get_system_config()


async def get_llm_config() -> dict[str, str]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await _rows(session, category=_LLM_CATEGORY)
    return {row[0]: str(row[1]) for row in rows}


async def update_llm_config(values: dict[str, str]) -> dict[str, str]:
    factory = get_session_factory()
    async with factory() as session:
        for key, value in values.items():
            await _upsert(session, key=key, value=str(value), category=_LLM_CATEGORY)
        await session.commit()
    return await get_llm_config()
